from __future__ import annotations

from collections import defaultdict, deque

import numpy as np
import pandas as pd

from ..data.ids import haversine_km
from .registry import production_registry
from .windows import FEATURE_SET_VERSION, FeatureWindows, LEAKAGE_COLUMNS


class _UF:
    def __init__(self):
        self.p: dict[str, str] = {}
        self.sz: dict[str, int] = {}

    def add(self, x: str):
        if x not in self.p:
            self.p[x] = x
            self.sz[x] = 1

    def find(self, x: str) -> str:
        self.add(x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.sz[ra] < self.sz[rb]:
            ra, rb = rb, ra
        self.p[rb] = ra
        self.sz[ra] += self.sz[rb]

    def size(self, x: str) -> int:
        return self.sz[self.find(x)]


def _is_return(row) -> bool:
    if str(row.get('channel') or '').upper() in {'RETURN', 'REFUND'}:
        return True
    val = row.get('is_return')
    return bool(val) and str(val) not in {'0', 'false', 'False'}


def _loc_xy(row) -> tuple[float, float] | None:
    lat, lon = row.get('latitude'), row.get('longitude')
    try:
        if lat is not None and lon is not None and not (pd.isna(lat) or pd.isna(lon)):
            return float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    return None


def _count_in_window(times: deque, now: pd.Timestamp, window: pd.Timedelta) -> int:
    cutoff = now - window
    while times and times[0] < cutoff:
        times.popleft()
    return len(times)


def _switch_velocity(events: deque, now: pd.Timestamp, window: pd.Timedelta, current=None) -> float:
    cutoff = now - window
    while events and events[0][0] < cutoff:
        events.popleft()
    seq = [e[1] for e in events if e[0] >= cutoff]
    if current is not None:
        seq.append(current)
    if len(seq) < 2:
        return 0.0
    switches = sum(1 for i in range(1, len(seq)) if seq[i] != seq[i - 1])
    hours = max(window.total_seconds() / 3600.0, 1.0 / 60.0)
    return switches / hours


def _mode_hour(hours: list[int]) -> int:
    counts = defaultdict(int)
    for h in hours:
        counts[h] += 1
    best = max(counts.values())
    return min(h for h, c in counts.items() if c == best)


def compute_features(df: pd.DataFrame, windows: FeatureWindows | None = None, registry=None) -> pd.DataFrame:
    """Point-in-time features. `label` may be present for historical rates but is never emitted."""
    windows = windows or FeatureWindows()
    registry = registry or production_registry()
    names = registry.names(production_only=True)
    if df is None or len(df) == 0:
        out = pd.DataFrame(columns=['transaction_id'] + names)
        out.attrs['feature_set_version'] = FEATURE_SET_VERSION
        out.attrs['feature_metadata'] = registry.metadata()
        return out

    work = df.copy()
    if 'transaction_id' not in work.columns and 'id' in work.columns:
        work['transaction_id'] = work['id']
    work['ts'] = pd.to_datetime(work['ts'], utc=True)
    work = work.sort_values(['ts', 'transaction_id'], kind='mergesort').reset_index(drop=True)

    opened = {}
    if 'opened_at' in work.columns:
        for rec in work[['account_id', 'opened_at']].drop_duplicates('account_id').itertuples(index=False):
            if rec.opened_at is not None and not pd.isna(rec.opened_at):
                opened[rec.account_id] = pd.Timestamp(rec.opened_at)
                if opened[rec.account_id].tzinfo is None:
                    opened[rec.account_id] = opened[rec.account_id].tz_localize('UTC')

    imei_accounts: dict[str, set] = defaultdict(set)
    imei_sims: dict[str, set] = defaultdict(set)
    imei_fraud: dict[str, int] = defaultdict(int)
    sim_devices: dict[str, set] = defaultdict(set)
    ip_accounts: dict[str, set] = defaultdict(set)
    ip_devices: dict[str, set] = defaultdict(set)
    first_imei: dict[str, pd.Timestamp] = {}
    first_sim: dict[str, pd.Timestamp] = {}
    first_acct: dict[str, pd.Timestamp] = {}
    acct_times: dict[str, deque] = defaultdict(deque)
    acct_imei_ev: dict[str, deque] = defaultdict(deque)
    acct_sim_ev: dict[str, deque] = defaultdict(deque)
    acct_ip_ev: dict[str, deque] = defaultdict(deque)
    acct_loc_ev: dict[str, deque] = defaultdict(deque)
    acct_amounts: dict[str, list] = defaultdict(list)
    acct_hours: dict[str, list] = defaultdict(list)
    acct_returns: dict[str, list] = defaultdict(list)
    acct_fraud: dict[str, list] = defaultdict(list)
    acct_devices: dict[str, set] = defaultdict(set)
    acct_ips: dict[str, set] = defaultdict(set)
    acct_locs_xy: dict[str, list] = defaultdict(list)
    merch_n: dict[str, int] = defaultdict(int)
    merch_fraud: dict[str, int] = defaultdict(int)
    cust_merch: dict[tuple, int] = defaultdict(int)
    acct_fraud_flag: set[str] = set()
    uf = _UF()
    linked: dict[str, set] = defaultdict(set)

    rows_out = []
    for rec in work.to_dict('records'):
        ts = rec['ts']
        acct = str(rec.get('account_id') or '')
        imei = str(rec.get('imei') or '')
        sim = str(rec.get('sim_id') or '')
        ip = str(rec.get('ip') or '')
        merch = str(rec.get('merchant_id') or '')
        cust = str(rec.get('customer_id') or '')
        loc = str(rec.get('location') or rec.get('location_code') or '')
        amount = float(rec.get('amount') or 0.0)
        hour = int(ts.hour)
        dow = int(ts.dayofweek)
        xy = _loc_xy(rec)

        past_amt = acct_amounts[acct]
        mean = float(np.mean(past_amt)) if past_amt else amount
        std = float(np.std(past_amt)) if len(past_amt) > 1 else 0.0
        amt_dev = 0.0 if not past_amt else (amount - mean) / max(std, 1e-6)

        n_acct = len(acct_fraud[acct])
        n_fr = sum(acct_fraud[acct])
        hist_fr = (n_fr / n_acct) if n_acct else 0.0
        ret = acct_returns[acct]
        ret_rate = (sum(ret) / len(ret)) if ret else 0.0
        merch_cnt = merch_n[merch]
        merch_fr = (merch_fraud[merch] / merch_cnt) if merch_cnt else 0.0

        first_i = first_imei.get(imei, ts)
        first_s = first_sim.get(sim, ts)
        first_a = opened.get(acct, first_acct.get(acct, ts))
        device_age = max(0.0, (ts - first_i).total_seconds() / 86400.0)
        sim_age = max(0.0, (ts - first_s).total_seconds() / 86400.0)
        account_age = max(0.0, (ts - first_a).total_seconds() / 86400.0)

        p1h = _count_in_window(acct_times[acct], ts, windows.purchases_short)
        p24 = _count_in_window(acct_times[acct], ts, windows.purchases_long)
        dev_sw = _switch_velocity(acct_imei_ev[acct], ts, windows.velocity, imei)
        sim_sw = _switch_velocity(acct_sim_ev[acct], ts, windows.velocity, sim)
        ip_sw = _switch_velocity(acct_ip_ev[acct], ts, windows.velocity, ip)

        loc_events = acct_loc_ev[acct]
        cutoff = ts - windows.velocity
        while loc_events and loc_events[0][0] < cutoff:
            loc_events.popleft()
        loc_codes = [e[1] for e in loc_events] + ([loc] if loc else [])
        loc_distinct = len(set(loc_codes))
        loc_vel = loc_distinct / max(windows.velocity.total_seconds() / 3600.0, 1.0 / 60.0)

        hist_xy = acct_locs_xy[acct]
        if xy and hist_xy:
            mlat = sum(p[0] for p in hist_xy) / len(hist_xy)
            mlon = sum(p[1] for p in hist_xy) / len(hist_xy)
            dist = haversine_km(mlat, mlon, xy[0], xy[1])
        elif loc and loc_events:
            dist = 0.0 if loc_events[-1][1] == loc else 500.0
        else:
            dist = 0.0
        unusual_loc = 1.0 if dist > windows.unusual_distance_km else 0.0

        hours = acct_hours[acct]
        if len(hours) >= windows.unusual_hour_min_history:
            mode = _mode_hour(hours)
            delta = min((hour - mode) % 24, (mode - hour) % 24)
            unusual_hour = 1.0 if delta >= windows.unusual_hour_delta else 0.0
        else:
            unusual_hour = 0.0

        neighbors = set(linked[acct])
        fraud_neighbors = len(neighbors & acct_fraud_flag)
        community = uf.size(acct) if acct in uf.p else 1

        feat = {
            'transaction_id': rec.get('transaction_id'),
            'accounts_per_imei': float(len(imei_accounts[imei])),
            'sims_per_imei': float(len(imei_sims[imei])),
            'device_switch_velocity': float(dev_sw),
            'device_age': float(device_age),
            'previous_fraud_links': float(imei_fraud[imei]),
            'sim_age': float(sim_age),
            'devices_per_sim': float(len(sim_devices[sim])),
            'sim_change_velocity': float(sim_sw),
            'account_age': float(account_age),
            'purchases_last_1h': float(p1h),
            'purchases_last_24h': float(p24),
            'historical_fraud_rate': float(hist_fr),
            'amount': float(amount),
            'amount_deviation': float(amt_dev),
            'transaction_velocity': float(p1h),
            'return_rate': float(ret_rate),
            'accounts_per_ip': float(len(ip_accounts[ip])),
            'devices_per_ip': float(len(ip_devices[ip])),
            'ip_change_velocity': float(ip_sw),
            'distance_from_normal_location': float(dist),
            'location_change_velocity': float(loc_vel),
            'unusual_location_indicator': float(unusual_loc),
            'merchant_transaction_count': float(merch_cnt),
            'merchant_fraud_rate': float(merch_fr),
            'customer_merchant_frequency': float(cust_merch[(cust, merch)]),
            'hour_of_day': hour,
            'day_of_week': dow,
            'unusual_hour_indicator': float(unusual_hour),
            'connected_accounts': float(len(neighbors)),
            'connected_devices': float(len(acct_devices[acct])),
            'connected_ips': float(len(acct_ips[acct])),
            'fraud_neighbor_count': float(fraud_neighbors),
            'community_size': float(community),
        }
        leak = set(feat) & LEAKAGE_COLUMNS
        if leak:
            raise RuntimeError(f'feature row leaked {leak}')
        rows_out.append(feat)

        # commit event to state AFTER scoring
        for other in list(imei_accounts[imei]):
            if other != acct:
                uf.union(acct, other)
                linked[acct].add(other)
                linked[other].add(acct)
        for other in list(ip_accounts[ip]):
            if other != acct:
                uf.union(acct, other)
                linked[acct].add(other)
                linked[other].add(acct)
        for other in list(imei_accounts[imei] | ip_accounts[ip]):
            if other != acct:
                linked[acct].add(other)
                linked[other].add(acct)

        imei_accounts[imei].add(acct)
        imei_sims[imei].add(sim)
        sim_devices[sim].add(imei)
        if ip:
            ip_accounts[ip].add(acct)
            ip_devices[ip].add(imei)
        first_imei.setdefault(imei, ts)
        first_sim.setdefault(sim, ts)
        first_acct.setdefault(acct, ts)
        acct_times[acct].append(ts)
        acct_imei_ev[acct].append((ts, imei))
        acct_sim_ev[acct].append((ts, sim))
        acct_ip_ev[acct].append((ts, ip))
        acct_loc_ev[acct].append((ts, loc))
        acct_amounts[acct].append(amount)
        acct_hours[acct].append(hour)
        is_ret = 1 if _is_return(rec) else 0
        acct_returns[acct].append(is_ret)
        lab = rec.get('label')
        is_fraud = 0
        if lab is not None and not (isinstance(lab, float) and np.isnan(lab)):
            try:
                is_fraud = 1 if int(lab) == 1 else 0
            except (TypeError, ValueError):
                is_fraud = 0
        acct_fraud[acct].append(is_fraud)
        if is_fraud:
            imei_fraud[imei] += 1
            acct_fraud_flag.add(acct)
            merch_fraud[merch] += 1
        merch_n[merch] += 1
        cust_merch[(cust, merch)] += 1
        acct_devices[acct].add(imei)
        if ip:
            acct_ips[acct].add(ip)
        if xy:
            acct_locs_xy[acct].append(xy)
        uf.add(acct)

    out = pd.DataFrame(rows_out)
    ordered = ['transaction_id'] + names
    out = out.loc[:, ordered]
    registry.require_no_leakage(out.columns)
    out.attrs['feature_set_version'] = FEATURE_SET_VERSION
    out.attrs['feature_metadata'] = registry.metadata()
    out.attrs['windows'] = {
        'purchases_short_seconds': windows.purchases_short.total_seconds(),
        'purchases_long_seconds': windows.purchases_long.total_seconds(),
        'velocity_seconds': windows.velocity.total_seconds(),
        'unusual_distance_km': windows.unusual_distance_km,
    }
    return out
