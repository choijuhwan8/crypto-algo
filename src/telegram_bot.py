"""
Telegram bot (long-polling, no webhook).

Commands
--------
/status    – equity, return, DD, pause/kill state
/positions – open positions with current z-score and unrealised PnL
/pnl       – closed trade statistics
/pause     – soft-pause trading
/resume    – lift pause
/closeall  – close all open positions immediately (calls close_all_callback)
/help      – command list

Alert helpers (called by the scheduler)
-----------------------------------------
alert_signal / alert_entry / alert_exit / alert_stop_loss /
alert_error / alert_kill / send_daily_summary
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, Optional

import httpx

from .config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


class TelegramBot:
    def __init__(
        self,
        risk_engine,
        state_manager,
        signal_service=None,
    ) -> None:
        self.risk = risk_engine
        self.state = state_manager
        self.sig = signal_service
        self._offset: int = 0
        self._running: bool = False
        self._close_all_cb: Optional[Callable[[], Coroutine]] = None

    def set_close_all_callback(self, cb: Callable[[], Coroutine]) -> None:
        self._close_all_cb = cb

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send(self, text: str, parse_mode: str = "Markdown") -> bool:
        """Send a message to the configured chat.  Returns True on success."""
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            logger.debug(f"Telegram not configured – skipping: {text[:60]}")
            return False
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(f"{_BASE}/sendMessage", json=payload)
                if r.status_code != 200:
                    logger.error(f"Telegram sendMessage error {r.status_code}: {r.text[:200]}")
                    return False
            return True
        except Exception as exc:
            logger.error(f"Telegram send failed: {exc}")
            return False

    # ------------------------------------------------------------------
    # Long-polling loop
    # ------------------------------------------------------------------

    async def poll(self) -> None:
        self._running = True
        logger.info("Telegram: polling started")
        while self._running:
            updates = await self._get_updates()
            for upd in updates:
                self._offset = upd["update_id"] + 1
                msg = upd.get("message", {})
                text = msg.get("text", "").strip()
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if not text:
                    continue
                if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
                    logger.warning(f"Ignoring message from unknown chat {chat_id}")
                    continue
                logger.info(f"Telegram cmd: {text}")
                await self._dispatch(text)
            await asyncio.sleep(1)

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    async def _dispatch(self, text: str) -> None:
        cmd = text.split()[0].lower().split("@")[0]
        handlers = {
            "/status": self._cmd_status,
            "/positions": self._cmd_positions,
            "/pnl": self._cmd_pnl,
            "/upnl": self._cmd_upnl,
            "/pause": self._cmd_pause,
            "/resume": self._cmd_resume,
            "/closeall": self._cmd_closeall,
            "/help": self._cmd_help,
        }
        handler = handlers.get(cmd)
        if handler:
            await handler()
        else:
            await self.send(f"Unknown command: `{cmd}`\nUse /help for the list.")

    async def _cmd_status(self) -> None:
        s = self.state.get_summary()
        r = self.risk.run_checks()
        msg = (
            "*Bot Status*\n"
            f"Equity:       `${s['equity']:>10,.2f}`\n"
            f"Return:       `{s['total_return_pct']:>+9.2f}%`\n"
            f"Drawdown:     `{s['current_dd_pct']:>9.1f}%`\n"
            f"Open pairs:   `{s['open_positions']:>9}`\n"
            f"Paused:       `{r['paused']}`\n"
            f"Kill switch:  `{r['kill_active']}`"
        )
        if r.get("kill_reason"):
            msg += f"\nReason: _{r['kill_reason']}_"
        await self.send(msg)

    async def _cmd_positions(self) -> None:
        positions = self.state.get_open_positions()
        if not positions:
            await self.send("No open positions.")
            return

        lines = ["*Open Positions*"]
        for pk, pos in positions.items():
            stats = self.sig.get_stats(pk) if self.sig else {}
            z = stats.get("zscore", float("nan")) if stats else float("nan")
            lines.append(
                f"`{pk}` `{pos.direction}`\n"
                f"  entry z={pos.entry_zscore:.2f} | now z={z:.2f} | "
                f"pnl=${pos.pnl:+.2f}"
            )
        await self.send("\n".join(lines))

    async def _cmd_pnl(self) -> None:
        s = self.state.get_summary()
        msg = (
            "*P&L Report*\n"
            f"Equity:        `${s['equity']:>10,.2f}`\n"
            f"Total return:  `{s['total_return_pct']:>+9.2f}%`\n"
            f"Trades closed: `{s['total_trades']:>9}`\n"
            f"Win rate:      `{s['win_rate_pct']:>9.1f}%`\n"
            f"Fees paid:     `${s['total_fees_paid']:>9.2f}`\n"
            f"Unrealised:    `${s['open_unrealized_pnl']:>+9.2f}`\n"
            f"Peak equity:   `${s['peak_equity']:>10,.2f}`\n"
            f"Current DD:    `{s['current_dd_pct']:>9.1f}%`"
        )
        await self.send(msg)

    async def _cmd_upnl(self) -> None:
        positions = self.state.get_open_positions()
        if not positions:
            await self.send("No open positions.")
            return
        lines = ["*Live Unrealised PnL*"]
        total = 0.0
        for pos in positions.values():
            # Fetch live price from Binance
            import urllib.request, json as _json
            try:
                url_a = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={pos.sym_a}USDT"
                url_b = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={pos.sym_b}USDT"
                pa = float(_json.loads(urllib.request.urlopen(url_a, timeout=5).read())["price"])
                pb = float(_json.loads(urllib.request.urlopen(url_b, timeout=5).read())["price"])
                if pos.direction == "LONG_SPREAD":
                    pnl_a = pos.notional_a * (pa - pos.entry_price_a) / pos.entry_price_a
                    pnl_b = pos.notional_b * (pos.entry_price_b - pb) / pos.entry_price_b
                else:
                    pnl_a = pos.notional_a * (pos.entry_price_a - pa) / pos.entry_price_a
                    pnl_b = pos.notional_b * (pb - pos.entry_price_b) / pos.entry_price_b
                upnl = pnl_a + pnl_b
                total += upnl
                icon = "🟢" if upnl >= 0 else "🔴"
                lines.append(
                    f"{icon} `{pos.pair_key}` `{pos.direction}`\n"
                    f"  Leg A: `${pnl_a:+.2f}` | Leg B: `${pnl_b:+.2f}` | Total: `${upnl:+.2f}`"
                )
            except Exception as exc:
                lines.append(f"`{pos.pair_key}` — price fetch failed: {exc}")
        lines.append(f"\n*Total uPnL: `${total:+.2f}`*")
        await self.send("\n".join(lines))

    async def _cmd_pause(self) -> None:
        self.risk.pause()
        await self.send("Bot *PAUSED*.  Use /resume to restart.")

    async def _cmd_resume(self) -> None:
        if self.risk.kill_active:
            await self.send(
                "Cannot resume: kill switch is active.  "
                "Restart the process to reset."
            )
            return
        self.risk.resume()
        await self.send("Bot *RESUMED*.")

    async def _cmd_closeall(self) -> None:
        await self.send("Closing all open positions…")
        if self._close_all_cb:
            await self._close_all_cb()
        else:
            await self.send("close_all callback not registered.")

    async def _cmd_help(self) -> None:
        await self.send(
            "*Commands*\n"
            "/status    – bot status\n"
            "/positions – open positions\n"
            "/pnl       – P&L summary\n"
            "/upnl      – live unrealised PnL (real-time)\n"
            "/pause     – pause trading\n"
            "/resume    – resume trading\n"
            "/closeall  – close all positions\n"
            "/help      – this message"
        )

    # ------------------------------------------------------------------
    # Alert helpers
    # ------------------------------------------------------------------

    async def alert_signal(self, pair_key: str, signal: str, zscore: float) -> None:
        await self.send(
            f"*Signal* `{signal}` on `{pair_key}`\nZ-score: `{zscore:.2f}`"
        )

    async def alert_entry(
        self, pair_key: str, direction: str, zscore: float, notional: float, fee: float
    ) -> None:
        await self.send(
            f"*ENTRY* `{direction}` on `{pair_key}`\n"
            f"Z-score: `{zscore:.2f}` | Notional: `${notional:,.0f}` | Fee: `${fee:.2f}`"
        )

    async def alert_exit(
        self, pair_key: str, pnl: float, reason: str
    ) -> None:
        icon = "✅" if pnl >= 0 else "❌"
        await self.send(
            f"{icon} *EXIT* `{pair_key}` | PnL: `${pnl:+.2f}` | Reason: `{reason}`"
        )

    async def alert_stop_loss(self, pair_key: str, pnl: float) -> None:
        await self.send(
            f"⛔ *STOP LOSS* triggered on `{pair_key}` | PnL: `${pnl:+.2f}`"
        )

    async def alert_error(self, msg: str) -> None:
        await self.send(f"🚨 *ERROR* {msg}")

    async def alert_kill(self, reason: str) -> None:
        await self.send(f"🔴 *KILL SWITCH ACTIVATED*\n{reason}")

    async def send_daily_summary(self) -> None:
        s = self.state.get_summary()
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await self.send(
            f"*Daily Summary – {date_str}*\n"
            f"Equity:       `${s['equity']:>10,.2f}`\n"
            f"Total return: `{s['total_return_pct']:>+9.2f}%`\n"
            f"Trades:       `{s['total_trades']:>9}`\n"
            f"Win rate:     `{s['win_rate_pct']:>9.1f}%`\n"
            f"Fees paid:    `${s['total_fees_paid']:>9.2f}`\n"
            f"Drawdown:     `{s['current_dd_pct']:>9.1f}%`"
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _get_updates(self) -> list:
        if not TELEGRAM_BOT_TOKEN:
            await asyncio.sleep(5)
            return []
        params = {"offset": self._offset, "timeout": 30, "limit": 10}
        try:
            async with httpx.AsyncClient(timeout=35) as client:
                r = await client.get(f"{_BASE}/getUpdates", params=params)
                return r.json().get("result", [])
        except Exception as exc:
            logger.warning(f"Telegram getUpdates failed: {exc}")
            await asyncio.sleep(5)
            return []
