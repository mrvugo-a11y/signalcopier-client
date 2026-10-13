"""MetaTrader 5 execution layer."""
import math
from .parser import Signal, Command

try:
    import MetaTrader5 as mt5
except ImportError:          # lets the parser/tests run on machines without MT5
    mt5 = None


class Executor:
    def __init__(self, cfg, log):
        self.cfg, self.log = cfg, log

    # ---------- connection ----------
    def connect(self):
        if mt5 is None:
            raise RuntimeError("MetaTrader5 package not installed (pip install MetaTrader5)")
        kw = {}
        if self.cfg.get("mt5_path"): kw["path"] = self.cfg["mt5_path"]
        if self.cfg.get("mt5_login"):
            kw.update(login=int(self.cfg["mt5_login"]), password=self.cfg.get("mt5_password", ""),
                      server=self.cfg.get("mt5_server", ""))
        if not mt5.initialize(**kw):
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
        ai = mt5.account_info()
        ti = mt5.terminal_info()
        self.log(f"Connected to MetaTrader 5: account {ai.login} at {ai.server}, balance {ai.balance} {ai.currency}")
        if ti and not ti.trade_allowed:
            self.log("WARNING: Algo Trading is switched off in MetaTrader 5. Click the Algo Trading button so it turns green, otherwise trades are rejected.")

    def shutdown(self):
        if mt5: mt5.shutdown()

    # ---------- symbols ----------
    def resolve_symbol(self, sym, st):
        smap = {k.upper(): v for k, v in st.get("symbol_map", {}).items()}
        sym = smap.get(sym.upper(), sym).upper()
        suffix = st.get("symbol_suffix", "")
        for cand in ([sym + suffix] if suffix else []) + [sym]:
            if mt5.symbol_info(cand) is not None:
                mt5.symbol_select(cand, True); return cand
        for s in mt5.symbols_get() or []:            # auto-detect broker suffix, e.g. XAUUSDm / XAUUSD.r
            n = s.name.upper()
            if n.startswith(sym) and len(n) - len(sym) <= 4:
                mt5.symbol_select(s.name, True); return s.name
        return None

    # ---------- helpers ----------
    @staticmethod
    def _filling(info):
        fm = info.filling_mode
        if fm & 1: return mt5.ORDER_FILLING_FOK
        if fm & 2: return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def _lot(self, symbol, info, side, entry, sl, n_parts, st):
        if st.get("lot_mode", "fixed") == "risk" and sl:
            acc = mt5.account_info()
            risk_money = acc.balance * float(st.get("risk_percent", 1)) / 100.0
            otype = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
            loss1 = mt5.order_calc_profit(otype, symbol, 1.0, entry, sl)
            if loss1 is None or loss1 == 0:
                ticks = abs(entry - sl) / info.trade_tick_size
                loss1 = ticks * info.trade_tick_value
            total = risk_money / abs(loss1) if loss1 else info.volume_min
            lot = total / n_parts
        else:
            lot = float(st.get("fixed_lot", 0.01))
            if st.get("fixed_lot_is_total"): lot /= n_parts
        step = info.volume_step or 0.01
        lot = math.floor(lot / step + 1e-9) * step
        lot = max(info.volume_min, min(info.volume_max, lot))
        return round(lot, 8)

    def _send(self, req):
        r = mt5.order_send(req)
        if r is None:
            self.log(f"MetaTrader 5 did not accept the order: {mt5.last_error()}"); return None
        if r.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED, mt5.TRADE_RETCODE_DONE_PARTIAL):
            self.log(f"Order rejected by the broker: {r.comment} (code {r.retcode})" + (". Algo Trading is off in MetaTrader 5." if r.retcode == 10027 else "")); return None
        return r

    def _my_count(self, st):
        mg = int(st.get("magic_number", 0))
        return sum(1 for p in (mt5.positions_get() or []) if p.magic == mg) + \
               sum(1 for o in (mt5.orders_get() or []) if o.magic == mg)

    # ---------- open ----------
    def open_signal(self, sig: Signal, st):
        symbol = self.resolve_symbol(sig.symbol, st)
        if not symbol:
            self.log(f"Skipped: your broker has no symbol called {sig.symbol}. Add it under Settings, Rename symbols."); return []
        info = mt5.symbol_info(symbol); tick = mt5.symbol_info_tick(symbol)
        pt, digits = info.point, info.digits
        side = sig.side
        if st.get("reverse_signals"):
            side = "SELL" if side == "BUY" else "BUY"
        spread = (tick.ask - tick.bid) / pt
        if st.get("max_spread_points") and spread > float(st["max_spread_points"]):
            self.log(f"Skipped {symbol}: the spread is {spread:.0f} points, your limit is {st['max_spread_points']}."); return []
        if self._my_count(st) >= int(st.get("max_open_trades", 999)):
            self.log("Skipped: the most trades open at once limit is reached."); return []
        market = tick.ask if side == "BUY" else tick.bid

        # decide order type and entry price
        otype, price = sig.order_type, sig.entry
        if sig.entry_high is not None:                      # entry range
            lo, hi = sig.entry, sig.entry_high
            mode = st.get("range_entry", "market")
            inside = lo <= market <= hi or (side == "BUY" and market < lo) or (side == "SELL" and market > hi)
            if otype == "MARKET" and (mode == "market" or inside):
                price = None
            else:
                price = (lo + hi) / 2 if mode == "mid" else (hi if side == "BUY" else lo)
                if otype == "MARKET": otype = "LIMIT"
        elif otype == "MARKET" and price is not None:
            maxd = float(st.get("max_entry_distance_points", 0) or 0)
            if maxd and abs(market - price) / pt > maxd:
                if not st.get("pending_orders", True):
                    self.log(f"Skipped {symbol}: the price moved too far from the entry {price}."); return []
                better = (side == "BUY" and price < market) or (side == "SELL" and price > market)
                otype = "LIMIT" if better else "STOP"
            else:
                price = None
        if otype in ("LIMIT", "STOP") and not st.get("pending_orders", True):
            self.log("Skipped a limit or stop order, because pending orders are switched off in Settings."); return []
        if otype == "MARKET": price = None
        ref = price if price is not None else market

        sl = sig.sl
        if st.get("reverse_signals"):
            sl, tps = (sig.tps[0] if sig.tps else None), [sig.sl]
        else:
            tps = list(sig.tps) or [None]
        if not sl and st.get("default_sl_points"):
            d = float(st["default_sl_points"]) * pt
            sl = ref - d if side == "BUY" else ref + d
        use = st.get("use_tps", [1, 2, 3, 4, 5])
        chosen = [tp for i, tp in enumerate(tps, 1) if i in use] or tps[:1]
        if not st.get("split_tps", True):
            chosen = chosen[:1]
        lot = self._lot(symbol, info, side, ref, sl, len(chosen), st)

        type_map = {("BUY", "MARKET"): mt5.ORDER_TYPE_BUY, ("SELL", "MARKET"): mt5.ORDER_TYPE_SELL,
                    ("BUY", "LIMIT"): mt5.ORDER_TYPE_BUY_LIMIT, ("SELL", "LIMIT"): mt5.ORDER_TYPE_SELL_LIMIT,
                    ("BUY", "STOP"): mt5.ORDER_TYPE_BUY_STOP, ("SELL", "STOP"): mt5.ORDER_TYPE_SELL_STOP}
        tickets = []
        for i, tp in enumerate(chosen, 1):
            req = {"action": mt5.TRADE_ACTION_DEAL if otype == "MARKET" else mt5.TRADE_ACTION_PENDING,
                   "symbol": symbol, "volume": lot, "type": type_map[(side, otype)],
                   "price": round(ref, digits), "deviation": int(st.get("slippage_points", 20)),
                   "magic": int(st.get("magic_number", 0)), "comment": f"{st.get('comment','TGC')} TP{i}"[:31],
                   "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(info)}
            if sl: req["sl"] = round(sl, digits)
            if tp: req["tp"] = round(tp, digits)
            r = self._send(req)
            if r:
                t = r.order or r.deal
                tickets.append(t)
                self.log(f"TRADE opened: {side} {symbol} {lot} lots at {req['price']}, stop loss {req.get('sl') or 'none'}, take profit {req.get('tp') or 'none'} (ticket {t})")
        return tickets

    # ---------- manage ----------
    def _position(self, t):
        p = mt5.positions_get(ticket=t)
        return p[0] if p else None

    def _order(self, t):
        o = mt5.orders_get(ticket=t)
        return o[0] if o else None

    def _modify(self, p, sl=None, tp=None):
        req = {"action": mt5.TRADE_ACTION_SLTP, "position": p.ticket, "symbol": p.symbol,
               "sl": sl if sl is not None else p.sl, "tp": tp if tp is not None else p.tp}
        if self._send(req): self.log(f"Trade {p.ticket} updated: stop loss {req['sl']}, take profit {req['tp']}")

    def _modify_order(self, o, sl=None, tp=None):
        req = {"action": mt5.TRADE_ACTION_MODIFY, "order": o.ticket, "price": o.price_open,
               "sl": sl if sl is not None else o.sl, "tp": tp if tp is not None else o.tp,
               "type_time": o.type_time}
        if self._send(req): self.log(f"Pending order {o.ticket} updated: stop loss {req['sl']}, take profit {req['tp']}")

    def _close(self, p, fraction=1.0):
        info = mt5.symbol_info(p.symbol); tick = mt5.symbol_info_tick(p.symbol)
        vol = p.volume * fraction
        step = info.volume_step or 0.01
        vol = round(math.floor(vol / step + 1e-9) * step, 8)
        if vol < info.volume_min:
            if fraction < 1: self.log(f"Trade {p.ticket} is too small to close only part of it, left open."); return
            vol = p.volume
        vol = min(vol, p.volume)
        buy = p.type == mt5.POSITION_TYPE_BUY
        req = {"action": mt5.TRADE_ACTION_DEAL, "position": p.ticket, "symbol": p.symbol, "volume": vol,
               "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
               "price": tick.bid if buy else tick.ask, "deviation": 30, "magic": p.magic,
               "type_filling": self._filling(info)}
        if self._send(req): self.log(f"TRADE closed: {vol} lots of trade {p.ticket} ({p.symbol})")

    def _cancel(self, o):
        if self._send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket}):
            self.log(f"Pending order {o.ticket} cancelled")

    def apply(self, cmd: Command, tickets, st):
        positions = [p for p in (self._position(t) for t in tickets) if p]
        orders = [o for o in (self._order(t) for t in tickets) if o]
        if not positions and not orders:
            self.log(f"{cmd.action.replace('_', ' ').lower()}: no open trades left for this signal"); return
        a = cmd.action
        if a == "CLOSE_ALL":
            for p in positions: self._close(p)
            for o in orders: self._cancel(o)
        elif a == "CANCEL":
            for o in orders: self._cancel(o)
        elif a in ("CLOSE_HALF", "CLOSE_PARTIAL"):
            frac = 0.5 if a == "CLOSE_HALF" else float(cmd.value)
            if len(positions) > 1:          # split trade: close that share of the trades (furthest TP last)
                n = max(1, round(len(positions) * frac))
                for p in positions[:n]: self._close(p)
            else:
                for p in positions: self._close(p, frac)
        elif a == "MOVE_SL_BE":
            for p in positions: self._modify(p, sl=p.price_open)
            for o in orders: self._modify_order(o, sl=o.price_open)
        elif a == "MOVE_SL":
            for p in positions: self._modify(p, sl=cmd.value)
            for o in orders: self._modify_order(o, sl=cmd.value)
        elif a == "TP_HIT":
            act = st.get("tp_hit_action", "move_sl_be")
            if act == "move_sl_be":
                for p in positions: self._modify(p, sl=p.price_open)
            elif act == "close_half":
                for p in positions: self._close(p, 0.5)
            elif act == "close_all":
                for p in positions: self._close(p)
            if act != "none":
                for o in orders: self._cancel(o)   # TP already reached -> pending entries are stale
        elif a == "SL_HIT":
            for p in positions: self._close(p)
            for o in orders: self._cancel(o)
        elif a == "MODIFY":
            items = positions + orders
            for i, it in enumerate(items):
                tp = None
                if cmd.tps:
                    tp = cmd.tps[min(i, len(cmd.tps) - 1)] if len(items) > 1 else cmd.tps[0]
                fn = self._modify if it in positions else self._modify_order
                fn(it, sl=cmd.sl, tp=tp)
