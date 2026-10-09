from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import json

import numpy as np
import pandas as pd

from ..config import GENERATED_DIR, PROCESSED_DIR, RAW_DIR, ensure_dirs
from ..ml.features import FEATURES
from .catalog import CITIES, FRAUD_PATTERNS, LEAKAGE_COLUMNS, MANUFACTURERS, MCC, SIZE_PRESETS, TARGET_COLUMNS
from .ids import IdFactory, haversine_km, make_imei

START = datetime(2026, 1, 1)
SPAN_MINUTES = 120 * 24 * 60


def pick(rng: np.random.Generator, seq):
    return seq[int(rng.integers(0, len(seq)))]


@dataclass
class DatasetBundle:
    customers: pd.DataFrame
    accounts: pd.DataFrame
    sims: pd.DataFrame
    handsets: pd.DataFrame
    merchants: pd.DataFrame
    networks: pd.DataFrame
    locations: pd.DataFrame
    transactions: pd.DataFrame
    fraud_cases: pd.DataFrame
    fraud_patterns: pd.DataFrame
    ground_truth: pd.DataFrame
    processed: pd.DataFrame
    manifest: dict

    def mvp_frame(self) -> pd.DataFrame:
        df = self.processed.copy()
        df['id'] = df['transaction_id']
        df['location'] = df['location_code']
        cols = ['id', 'customer_id', 'account_id', 'sim_id', 'imei', 'ip', 'merchant_id',
                'location', 'amount', 'ts', 'label', *FEATURES]
        return df.loc[:, list(dict.fromkeys(cols))]


class SyntheticFraudWorld:
    def __init__(self, n_transactions: int, seed: int):
        if n_transactions < 1:
            raise ValueError('n_transactions must be >= 1')
        self.n = int(n_transactions)
        self.seed = int(seed)
        self.rng = np.random.default_rng(self.seed)
        self.ids = IdFactory()
        self.locations: list[dict] = []
        self.customers: list[dict] = []
        self.accounts: list[dict] = []
        self.sims: list[dict] = []
        self.handsets: list[dict] = []
        self.merchants: list[dict] = []
        self.networks: list[dict] = []
        self.txns: list[dict] = []
        self.truth: list[dict] = []
        self.cases: list[dict] = []
        self.cust_by_id: dict[str, dict] = {}
        self.acct_by_id: dict[str, dict] = {}
        self.sim_by_id: dict[str, dict] = {}
        self.hand_by_id: dict[str, dict] = {}
        self.merch_by_id: dict[str, dict] = {}
        self.net_by_id: dict[str, dict] = {}
        self.loc_by_id: dict[str, dict] = {}
        self.accounts_by_customer: dict[str, list[str]] = defaultdict(list)

    def build(self) -> DatasetBundle:
        self._locations()
        self._merchants()
        self._networks()
        self._customers_graph()
        n_fraud = self._fraud_budget()
        n_legit = max(1, self.n - n_fraud)
        self._legitimate(n_legit)
        self._inject_all(n_fraud)
        self._trim_to_n()
        loc_df = pd.DataFrame(self.locations)
        cust_df = pd.DataFrame(self.customers)
        acct_df = pd.DataFrame(self.accounts)
        sim_df = pd.DataFrame(self.sims)
        hand_df = pd.DataFrame(self.handsets)
        merch_df = pd.DataFrame(self.merchants)
        net_df = pd.DataFrame(self.networks)
        txn_df = pd.DataFrame(self.txns)
        truth_df = pd.DataFrame(self.truth)
        cases_df = pd.DataFrame(self.cases) if self.cases else pd.DataFrame(columns=[
            'case_id', 'pattern_id', 'pattern_code', 'status', 'n_transactions', 'entity_refs',
        ])
        patterns_df = pd.DataFrame([
            {'pattern_id': pid, 'pattern_code': code, 'name': name, 'state': 'CATALOG'}
            for pid, code, name in FRAUD_PATTERNS
        ])
        processed = self._processed_frame(txn_df, truth_df, acct_df, merch_df, cust_df, loc_df)
        leak = sorted(set(processed.columns) & LEAKAGE_COLUMNS)
        if leak:
            raise RuntimeError(f'processed frame contains leakage columns: {leak}')
        manifest = {
            'n_transactions': int(len(processed)),
            'seed': self.seed,
            'fraud_rate': float(processed['label'].mean()) if len(processed) else 0.0,
            'n_customers': len(cust_df),
            'n_accounts': len(acct_df),
            'n_sims': len(sim_df),
            'n_handsets': len(hand_df),
            'n_merchants': len(merch_df),
            'n_networks': len(net_df),
            'n_locations': len(loc_df),
            'n_fraud_cases': len(cases_df),
            'patterns': [p[1] for p in FRAUD_PATTERNS],
            'size_presets': list(SIZE_PRESETS),
            'target_column': 'label',
            'feature_columns': FEATURES,
            'leakage_columns_excluded': sorted(LEAKAGE_COLUMNS),
        }
        source_tx = txn_df.copy()
        if 'label' in source_tx.columns:
            source_tx = source_tx.drop(columns=['label'])
        return DatasetBundle(
            customers=cust_df, accounts=acct_df, sims=sim_df, handsets=hand_df,
            merchants=merch_df, networks=net_df, locations=loc_df,
            transactions=source_tx, fraud_cases=cases_df, fraud_patterns=patterns_df,
            ground_truth=truth_df, processed=processed, manifest=manifest,
        )

    def _fraud_budget(self) -> int:
        return max(10 if self.n >= 80 else max(1, self.n // 10), int(self.n * 0.07))

    def _rand_ts(self, offset_min: int = 0, span: int | None = None) -> datetime:
        span = SPAN_MINUTES if span is None else span
        return START + timedelta(minutes=int(self.rng.integers(offset_min, max(offset_min + 1, span))))

    def _locations(self):
        for code, name, lat, lon, country in CITIES:
            row = {
                'location_id': f'LOC-{code}',
                'location_code': code,
                'city': name,
                'latitude': lat,
                'longitude': lon,
                'country': country,
            }
            self.locations.append(row)
            self.loc_by_id[row['location_id']] = row

    def _merchants(self):
        n_merch = min(420, max(24, self.n // 80))
        for _ in range(n_merch):
            mcc_id, category, mu, sigma = pick(self.rng, MCC)
            prior = float(np.clip(self.rng.beta(2, 18) + (0.12 if category in {'gift_cards', 'crypto_otc'} else 0), 0, 0.35))
            loc = pick(self.rng, self.locations)
            row = {
                'merchant_id': self.ids.next('M'),
                'mcc': mcc_id,
                'category': category,
                'country': loc['country'],
                'home_location_id': loc['location_id'],
                'amount_mu': mu,
                'amount_sigma': sigma,
                'prior_risk': round(prior, 4),
            }
            self.merchants.append(row)
            self.merch_by_id[row['merchant_id']] = row

    def _networks(self):
        n_ip = max(40, self.n // 12)
        for _ in range(n_ip):
            loc = pick(self.rng, self.locations)
            a = int(self.rng.integers(11, 220))
            b = int(self.rng.integers(0, 256))
            c = int(self.rng.integers(0, 256))
            d = int(self.rng.integers(1, 254))
            row = {
                'network_id': self.ids.next('N'),
                'ip': f'{a}.{b}.{c}.{d}',
                'asn': int(self.rng.integers(1000, 65000)),
                'connection_type': pick(self.rng, ('fiber', 'lte', '5g', 'dsl', 'public_wifi')),
                'home_location_id': loc['location_id'],
            }
            self.networks.append(row)
            self.net_by_id[row['network_id']] = row

    def _customers_graph(self):
        n_cust = max(25, min(self.n, self.n // 8 + 20))
        home_ips = list(self.networks)
        for _ in range(n_cust):
            loc = pick(self.rng, self.locations)
            cid = self.ids.next('C')
            tenure = int(self.rng.integers(30, 2400))
            cust = {
                'customer_id': cid,
                'segment': pick(self.rng, ('consumer', 'consumer', 'consumer', 'sme')),
                'home_location_id': loc['location_id'],
                'typical_hour': int(self.rng.integers(8, 22)),
                'tenure_days': tenure,
                'primary_sim_id': None,
                'primary_imei': None,
                'home_network_ids': [],
            }
            n_acct = int(self.rng.integers(1, 4))
            n_sim = max(n_acct, int(self.rng.integers(1, 4)))
            n_dev = max(1, int(self.rng.integers(1, 3)))
            nets = [pick(self.rng, home_ips)['network_id'] for _ in range(int(self.rng.integers(1, 4)))]
            cust['home_network_ids'] = list(dict.fromkeys(nets))
            sims, hands, accts = [], [], []
            for _s in range(n_sim):
                msisdn = '91' + ''.join(str(int(self.rng.integers(0, 10))) for _ in range(10))
                sim = {
                    'sim_id': self.ids.next('S'),
                    'msisdn': msisdn,
                    'customer_id': cid,
                    'status': 'ACTIVE',
                    'activated_at': (START - timedelta(days=int(self.rng.integers(1, tenure)))).isoformat(),
                }
                self.sims.append(sim)
                self.sim_by_id[sim['sim_id']] = sim
                sims.append(sim['sim_id'])
            for _h in range(n_dev):
                tac, mfr = pick(self.rng, MANUFACTURERS)
                tac8 = tac + ''.join(str(int(self.rng.integers(0, 10))) for _ in range(6))
                hand = {
                    'imei': make_imei(self.rng, tac8),
                    'manufacturer': mfr,
                    'model': f'{mfr}-{int(self.rng.integers(100, 999))}',
                    'customer_id': cid,
                    'first_seen': (START - timedelta(days=int(self.rng.integers(1, tenure)))).isoformat(),
                }
                self.handsets.append(hand)
                self.hand_by_id[hand['imei']] = hand
                hands.append(hand['imei'])
            for i in range(n_acct):
                opened = START - timedelta(days=int(self.rng.integers(1, tenure)))
                acct = {
                    'account_id': self.ids.next('A'),
                    'customer_id': cid,
                    'product_type': pick(self.rng, ('prepaid', 'postpaid', 'wallet')),
                    'status': 'ACTIVE',
                    'opened_at': opened.isoformat(),
                    'primary_sim_id': sims[i % len(sims)],
                    'primary_imei': hands[i % len(hands)],
                }
                self.accounts.append(acct)
                self.acct_by_id[acct['account_id']] = acct
                self.accounts_by_customer[cid].append(acct['account_id'])
                accts.append(acct['account_id'])
            cust['primary_sim_id'] = sims[0]
            cust['primary_imei'] = hands[0]
            cust['sim_ids'] = sims
            cust['imei_ids'] = hands
            cust['account_ids'] = accts
            self.customers.append(cust)
            self.cust_by_id[cid] = cust

    def _txn(self, ts: datetime, account_id: str, sim_id: str, imei: str, merchant_id: str,
             network_id: str, location_id: str, amount: float, channel: str = 'APP') -> dict:
        acct = self.acct_by_id[account_id]
        net = self.net_by_id[network_id]
        loc = self.loc_by_id[location_id]
        sim = self.sim_by_id[sim_id]
        return {
            'transaction_id': self.ids.next('T'),
            'customer_id': acct['customer_id'],
            'account_id': account_id,
            'sim_id': sim_id,
            'msisdn': sim['msisdn'],
            'imei': imei,
            'merchant_id': merchant_id,
            'network_id': network_id,
            'ip': net['ip'],
            'location_id': location_id,
            'location_code': loc['location_code'],
            'latitude': loc['latitude'],
            'longitude': loc['longitude'],
            'amount': round(float(amount), 2),
            'ts': ts.isoformat(timespec='seconds'),
            'channel': channel,
        }

    def _legit_amount(self, merchant: dict) -> float:
        return float(max(5.0, self.rng.lognormal(merchant['amount_mu'], merchant['amount_sigma'])))

    def _legitimate(self, n: int):
        custs = self.customers
        merchs = self.merchants
        for _ in range(n):
            cust = pick(self.rng, custs)
            acct_id = pick(self.rng, cust['account_ids'])
            acct = self.acct_by_id[acct_id]
            sim_id = acct['primary_sim_id']
            imei = acct['primary_imei']
            if self.rng.random() < 0.04:
                sim_id = pick(self.rng, cust['sim_ids'])
            if self.rng.random() < 0.03:
                imei = pick(self.rng, cust['imei_ids'])
            loc_id = cust['home_location_id']
            if self.rng.random() < 0.08:
                loc_id = pick(self.rng, self.locations)['location_id']
            net_id = pick(self.rng, cust['home_network_ids'])
            merch = pick(self.rng, merchs)
            hour = int(np.clip(int(self.rng.normal(cust['typical_hour'], 2.5)), 0, 23))
            ts = self._rand_ts()
            ts = ts.replace(hour=hour, minute=int(self.rng.integers(0, 60)), second=int(self.rng.integers(0, 60)))
            self.txns.append(self._txn(ts, acct_id, sim_id, imei, merch['merchant_id'], net_id, loc_id, self._legit_amount(merch)))

    def _new_case(self, pattern_id: str, pattern_code: str, refs: dict, n_tx: int) -> str:
        case_id = self.ids.next('FC')
        self.cases.append({
            'case_id': case_id,
            'pattern_id': pattern_id,
            'pattern_code': pattern_code,
            'status': 'GROUND_TRUTH_INJECTED',
            'n_transactions': n_tx,
            'entity_refs': json.dumps(refs, default=str),
        })
        return case_id

    def _mark(self, txns: list[dict], pattern_id: str, pattern_code: str, reason: str, case_id: str, injection_id: str):
        for t in txns:
            self.txns.append(t)
            self.truth.append({
                'transaction_id': t['transaction_id'],
                'label': 1,
                'pattern_id': pattern_id,
                'pattern_code': pattern_code,
                'case_id': case_id,
                'injection_id': injection_id,
                'fraud_reason': reason,
            })

    def _mule_account(self, loc_id: str | None = None) -> dict:
        loc_id = loc_id or pick(self.rng, self.locations)['location_id']
        cid = self.ids.next('C')
        tenure = int(self.rng.integers(3, 40))
        msisdn = '91' + ''.join(str(int(self.rng.integers(0, 10))) for _ in range(10))
        sim = {
            'sim_id': self.ids.next('S'), 'msisdn': msisdn, 'customer_id': cid,
            'status': 'ACTIVE', 'activated_at': (START - timedelta(days=tenure)).isoformat(),
        }
        self.sims.append(sim)
        self.sim_by_id[sim['sim_id']] = sim
        tac, mfr = pick(self.rng, MANUFACTURERS)
        tac8 = tac + ''.join(str(int(self.rng.integers(0, 10))) for _ in range(6))
        hand = {
            'imei': make_imei(self.rng, tac8), 'manufacturer': mfr,
            'model': f'{mfr}-MULE-{int(self.rng.integers(10, 99))}',
            'customer_id': cid, 'first_seen': (START - timedelta(days=tenure)).isoformat(),
        }
        self.handsets.append(hand)
        self.hand_by_id[hand['imei']] = hand
        net = pick(self.rng, self.networks)
        cust = {
            'customer_id': cid, 'segment': 'mule', 'home_location_id': loc_id,
            'typical_hour': int(self.rng.integers(0, 24)), 'tenure_days': tenure,
            'primary_sim_id': sim['sim_id'], 'primary_imei': hand['imei'],
            'home_network_ids': [net['network_id']], 'sim_ids': [sim['sim_id']],
            'imei_ids': [hand['imei']], 'account_ids': [],
        }
        opened = START - timedelta(days=tenure)
        acct = {
            'account_id': self.ids.next('A'), 'customer_id': cid, 'product_type': 'prepaid',
            'status': 'ACTIVE', 'opened_at': opened.isoformat(),
            'primary_sim_id': sim['sim_id'], 'primary_imei': hand['imei'],
        }
        self.accounts.append(acct)
        self.acct_by_id[acct['account_id']] = acct
        cust['account_ids'] = [acct['account_id']]
        self.customers.append(cust)
        self.cust_by_id[cid] = cust
        self.accounts_by_customer[cid].append(acct['account_id'])
        return {'customer': cust, 'account': acct, 'sim': sim, 'handset': hand, 'network': net}

    def _inject_all(self, n_fraud: int):
        weights = np.array([1.0, 1.0, 1.0, 1.0, 1.0, 1.1, 1.0, 1.0, 1.0, 1.4], dtype=float)
        weights /= weights.sum()
        if self.n >= 200 and n_fraud >= 10:
            extra = self.rng.multinomial(n_fraud - 10, weights)
            counts = np.ones(10, dtype=int) + extra
        else:
            counts = self.rng.multinomial(n_fraud, weights)
        injectors = [
            self._p_shared_imei_accounts,
            self._p_shared_imei_sims,
            self._p_rapid_sim,
            self._p_rapid_device,
            self._p_shared_ip,
            self._p_velocity,
            self._p_amount,
            self._p_travel,
            self._p_merchant,
            self._p_ring,
        ]
        for fn, k in zip(injectors, counts):
            if k > 0:
                fn(int(k))

    def _burst_ts(self, n: int, start: datetime, gap_seconds: int) -> list[datetime]:
        return [start + timedelta(seconds=i * gap_seconds) for i in range(n)]

    def _p_shared_imei_accounts(self, k: int):
        pid, code, _ = FRAUD_PATTERNS[0]
        inj = self.ids.next('INJ')
        device = self._mule_account()
        imei = device['handset']['imei']
        n_accts = max(3, min(12, k // 2 + 3))
        mules = [device] + [self._mule_account() for _ in range(n_accts - 1)]
        for m in mules:
            m['account']['primary_imei'] = imei
        txns = []
        start = self._rand_ts()
        merch = pick(self.rng, self.merchants)
        per = max(1, k // n_accts)
        for i, m in enumerate(mules):
            for j in range(per):
                ts = start + timedelta(minutes=i * 8 + j)
                txns.append(self._txn(
                    ts, m['account']['account_id'], m['sim']['sim_id'], imei,
                    merch['merchant_id'], m['network']['network_id'], m['customer']['home_location_id'],
                    self._legit_amount(merch) * float(self.rng.uniform(1.2, 3.0)),
                ))
        txns = txns[:k] if len(txns) >= k else txns
        while len(txns) < k:
            m = pick(self.rng, mules)
            txns.append(self._txn(
                start + timedelta(minutes=len(txns)), m['account']['account_id'], m['sim']['sim_id'], imei,
                merch['merchant_id'], m['network']['network_id'], m['customer']['home_location_id'],
                self._legit_amount(merch) * 2,
            ))
        case = self._new_case(pid, code, {'imei': imei, 'n_accounts': n_accts}, len(txns))
        self._mark(txns, pid, code, f'{n_accts} accounts transacted on shared IMEI {imei}', case, inj)

    def _p_shared_imei_sims(self, k: int):
        pid, code, _ = FRAUD_PATTERNS[1]
        inj = self.ids.next('INJ')
        host = self._mule_account()
        imei = host['handset']['imei']
        n_sims = max(4, min(10, k // 2 + 3))
        extra_sims = []
        for _ in range(n_sims - 1):
            msisdn = '91' + ''.join(str(int(self.rng.integers(0, 10))) for _ in range(10))
            sim = {
                'sim_id': self.ids.next('S'), 'msisdn': msisdn, 'customer_id': host['customer']['customer_id'],
                'status': 'ACTIVE', 'activated_at': START.isoformat(),
            }
            self.sims.append(sim)
            self.sim_by_id[sim['sim_id']] = sim
            extra_sims.append(sim)
        sims = [host['sim']] + extra_sims
        start = self._rand_ts()
        merch = pick(self.rng, self.merchants)
        txns = []
        for i in range(k):
            sim = sims[i % len(sims)]
            txns.append(self._txn(
                start + timedelta(minutes=i * 4), host['account']['account_id'], sim['sim_id'], imei,
                merch['merchant_id'], host['network']['network_id'], host['customer']['home_location_id'],
                self._legit_amount(merch) * float(self.rng.uniform(1.1, 2.5)),
            ))
        case = self._new_case(pid, code, {'imei': imei, 'n_sims': len(sims)}, len(txns))
        self._mark(txns, pid, code, f'{len(sims)} SIMs used on IMEI {imei}', case, inj)

    def _p_rapid_sim(self, k: int):
        pid, code, _ = FRAUD_PATTERNS[2]
        inj = self.ids.next('INJ')
        host = pick(self.rng, self.customers)
        acct_id = host['account_ids'][0]
        acct = self.acct_by_id[acct_id]
        n_sims = max(4, min(8, k))
        sims = []
        for _ in range(n_sims):
            msisdn = '91' + ''.join(str(int(self.rng.integers(0, 10))) for _ in range(10))
            sim = {
                'sim_id': self.ids.next('S'), 'msisdn': msisdn, 'customer_id': host['customer_id'],
                'status': 'ACTIVE', 'activated_at': START.isoformat(),
            }
            self.sims.append(sim)
            self.sim_by_id[sim['sim_id']] = sim
            sims.append(sim)
        start = self._rand_ts()
        merch = pick(self.rng, self.merchants)
        net_id = pick(self.rng, host['home_network_ids'])
        txns = []
        for i in range(k):
            sim = sims[i % len(sims)]
            txns.append(self._txn(
                start + timedelta(minutes=i * 3), acct_id, sim['sim_id'], acct['primary_imei'],
                merch['merchant_id'], net_id, host['home_location_id'], self._legit_amount(merch),
            ))
        case = self._new_case(pid, code, {'account_id': acct_id, 'n_sims': n_sims}, len(txns))
        self._mark(txns, pid, code, f'Account {acct_id} switched {n_sims} SIMs within hours', case, inj)

    def _p_rapid_device(self, k: int):
        pid, code, _ = FRAUD_PATTERNS[3]
        inj = self.ids.next('INJ')
        host = pick(self.rng, self.customers)
        acct_id = host['account_ids'][0]
        acct = self.acct_by_id[acct_id]
        n_dev = max(4, min(8, k))
        devices = []
        for _ in range(n_dev):
            tac, mfr = pick(self.rng, MANUFACTURERS)
            tac8 = tac + ''.join(str(int(self.rng.integers(0, 10))) for _ in range(6))
            hand = {
                'imei': make_imei(self.rng, tac8), 'manufacturer': mfr,
                'model': f'{mfr}-SW-{int(self.rng.integers(10, 99))}',
                'customer_id': host['customer_id'], 'first_seen': START.isoformat(),
            }
            self.handsets.append(hand)
            self.hand_by_id[hand['imei']] = hand
            devices.append(hand)
        start = self._rand_ts()
        merch = pick(self.rng, self.merchants)
        net_id = pick(self.rng, host['home_network_ids'])
        txns = []
        for i in range(k):
            hand = devices[i % len(devices)]
            txns.append(self._txn(
                start + timedelta(minutes=i * 3), acct_id, acct['primary_sim_id'], hand['imei'],
                merch['merchant_id'], net_id, host['home_location_id'], self._legit_amount(merch),
            ))
        case = self._new_case(pid, code, {'account_id': acct_id, 'n_devices': n_dev}, len(txns))
        self._mark(txns, pid, code, f'Account {acct_id} switched {n_dev} devices within hours', case, inj)

    def _p_shared_ip(self, k: int):
        pid, code, _ = FRAUD_PATTERNS[4]
        inj = self.ids.next('INJ')
        net = pick(self.rng, self.networks)
        n_accts = max(4, min(12, k // 2 + 3))
        mules = [self._mule_account() for _ in range(n_accts)]
        start = self._rand_ts()
        merch = pick(self.rng, self.merchants)
        txns = []
        i = 0
        while len(txns) < k:
            m = mules[i % len(mules)]
            txns.append(self._txn(
                start + timedelta(minutes=len(txns) * 2), m['account']['account_id'], m['sim']['sim_id'],
                m['handset']['imei'], merch['merchant_id'], net['network_id'], m['customer']['home_location_id'],
                self._legit_amount(merch) * float(self.rng.uniform(1.3, 4.0)),
            ))
            i += 1
        case = self._new_case(pid, code, {'ip': net['ip'], 'n_accounts': n_accts}, len(txns))
        self._mark(txns, pid, code, f'{n_accts} accounts originated from shared IP {net["ip"]}', case, inj)

    def _p_velocity(self, k: int):
        pid, code, _ = FRAUD_PATTERNS[5]
        inj = self.ids.next('INJ')
        host = self._mule_account()
        start = self._rand_ts()
        merch = pick(self.rng, self.merchants)
        txns = []
        for i, ts in enumerate(self._burst_ts(k, start, gap_seconds=25)):
            txns.append(self._txn(
                ts, host['account']['account_id'], host['sim']['sim_id'], host['handset']['imei'],
                merch['merchant_id'], host['network']['network_id'], host['customer']['home_location_id'],
                float(max(8.0, self.rng.lognormal(4.2, 0.4))),
            ))
        case = self._new_case(pid, code, {'account_id': host['account']['account_id']}, len(txns))
        self._mark(txns, pid, code, f'{k} transactions in ~{k * 25}s on account {host["account"]["account_id"]}', case, inj)

    def _p_amount(self, k: int):
        pid, code, _ = FRAUD_PATTERNS[6]
        inj = self.ids.next('INJ')
        host = pick(self.rng, self.customers)
        acct_id = host['account_ids'][0]
        acct = self.acct_by_id[acct_id]
        merch = pick(self.rng, self.merchants)
        net_id = pick(self.rng, host['home_network_ids'])
        start = self._rand_ts()
        txns = []
        for i in range(k):
            amount = float(self.rng.uniform(80000, 350000))
            txns.append(self._txn(
                start + timedelta(hours=i * 3), acct_id, acct['primary_sim_id'], acct['primary_imei'],
                merch['merchant_id'], net_id, host['home_location_id'], amount, channel='WEB',
            ))
        case = self._new_case(pid, code, {'account_id': acct_id}, len(txns))
        self._mark(txns, pid, code, f'Account {acct_id} posted out-of-profile amounts up to {max(t["amount"] for t in txns):.2f}', case, inj)

    def _p_travel(self, k: int):
        pid, code, _ = FRAUD_PATTERNS[7]
        inj = self.ids.next('INJ')
        host = pick(self.rng, self.customers)
        acct_id = host['account_ids'][0]
        acct = self.acct_by_id[acct_id]
        far = [loc for loc in self.locations if loc['location_id'] != host['home_location_id']]
        merch = pick(self.rng, self.merchants)
        net_home = pick(self.rng, host['home_network_ids'])
        net_far = pick(self.rng, self.networks)
        txns = []
        n_pairs = max(1, k // 2)
        for i in range(n_pairs):
            start = self._rand_ts()
            dest = pick(self.rng, far)
            txns.append(self._txn(
                start, acct_id, acct['primary_sim_id'], acct['primary_imei'],
                merch['merchant_id'], net_home, host['home_location_id'], self._legit_amount(merch),
            ))
            txns.append(self._txn(
                start + timedelta(minutes=int(self.rng.integers(8, 25))), acct_id, acct['primary_sim_id'],
                acct['primary_imei'], merch['merchant_id'], net_far['network_id'], dest['location_id'],
                self._legit_amount(merch) * 1.4,
            ))
        txns = txns[:k]
        while len(txns) < k:
            dest = pick(self.rng, far)
            start = self._rand_ts()
            txns.append(self._txn(
                start, acct_id, acct['primary_sim_id'], acct['primary_imei'],
                merch['merchant_id'], net_far['network_id'], dest['location_id'], self._legit_amount(merch) * 2,
            ))
        case = self._new_case(pid, code, {'account_id': acct_id}, len(txns))
        self._mark(txns, pid, code, f'Account {acct_id} transacted across distant cities faster than possible travel', case, inj)

    def _p_merchant(self, k: int):
        pid, code, _ = FRAUD_PATTERNS[8]
        inj = self.ids.next('INJ')
        risky = [m for m in self.merchants if m['category'] in {'gift_cards', 'crypto_otc', 'electronics'}]
        merch = pick(self.rng, risky or self.merchants)
        n_accts = max(3, min(8, k // 3 + 2))
        mules = [self._mule_account(merch['home_location_id']) for _ in range(n_accts)]
        start = self._rand_ts()
        txns = []
        i = 0
        while len(txns) < k:
            m = mules[i % len(mules)]
            txns.append(self._txn(
                start + timedelta(minutes=len(txns) * 6), m['account']['account_id'], m['sim']['sim_id'],
                m['handset']['imei'], merch['merchant_id'], m['network']['network_id'], merch['home_location_id'],
                float(self.rng.uniform(5000, 45000)),
            ))
            i += 1
        case = self._new_case(pid, code, {'merchant_id': merch['merchant_id']}, len(txns))
        self._mark(txns, pid, code, f'Merchant {merch["merchant_id"]} ({merch["category"]}) colluded with {n_accts} mule accounts', case, inj)

    def _p_ring(self, k: int):
        pid, code, _ = FRAUD_PATTERNS[9]
        inj = self.ids.next('INJ')
        shared_net = pick(self.rng, self.networks)
        tac, mfr = pick(self.rng, MANUFACTURERS)
        tac8 = tac + ''.join(str(int(self.rng.integers(0, 10))) for _ in range(6))
        shared_imei_hand = {
            'imei': make_imei(self.rng, tac8), 'manufacturer': mfr, 'model': f'{mfr}-RING',
            'customer_id': None, 'first_seen': START.isoformat(),
        }
        self.handsets.append(shared_imei_hand)
        self.hand_by_id[shared_imei_hand['imei']] = shared_imei_hand
        merch = pick(self.rng, self.merchants)
        n_members = max(4, min(9, k // 3 + 3))
        members = [self._mule_account() for _ in range(n_members)]
        start = self._rand_ts()
        txns = []
        i = 0
        while len(txns) < k:
            m = members[i % len(members)]
            use_shared_imei = i % 2 == 0
            imei = shared_imei_hand['imei'] if use_shared_imei else m['handset']['imei']
            txns.append(self._txn(
                start + timedelta(minutes=len(txns) * 5), m['account']['account_id'], m['sim']['sim_id'],
                imei, merch['merchant_id'], shared_net['network_id'], m['customer']['home_location_id'],
                float(self.rng.uniform(2000, 28000)),
            ))
            i += 1
        case = self._new_case(
            pid, code,
            {'imei': shared_imei_hand['imei'], 'ip': shared_net['ip'], 'n_members': n_members, 'merchant_id': merch['merchant_id']},
            len(txns),
        )
        self._mark(
            txns, pid, code,
            f'Ring of {n_members} accounts sharing IMEI {shared_imei_hand["imei"]} and IP {shared_net["ip"]}',
            case, inj,
        )

    def _trim_to_n(self):
        if len(self.txns) == self.n:
            return
        if len(self.txns) > self.n:
            legit_idx = [i for i, t in enumerate(self.txns) if t['transaction_id'] not in {r['transaction_id'] for r in self.truth}]
            drop = len(self.txns) - self.n
            for i in reversed(legit_idx[-drop:]):
                self.txns.pop(i)
            keep = {t['transaction_id'] for t in self.txns}
            self.truth = [r for r in self.truth if r['transaction_id'] in keep]
        while len(self.txns) < self.n:
            self._legitimate(1)

    def _processed_frame(self, txn_df, truth_df, acct_df, merch_df, cust_df, loc_df) -> pd.DataFrame:
        df = txn_df.copy()
        df['ts_dt'] = pd.to_datetime(df['ts'])
        df = df.sort_values('ts_dt').reset_index(drop=True)
        opened = {r['account_id']: pd.Timestamp(r['opened_at']) for r in acct_df.to_dict('records')}
        prior = {r['merchant_id']: float(r['prior_risk']) for r in merch_df.to_dict('records')}
        typical = {r['customer_id']: int(r['typical_hour']) for r in cust_df.to_dict('records')}
        loc_xy = {r['location_id']: (float(r['latitude']), float(r['longitude'])) for r in loc_df.to_dict('records')}
        fraud_ids = set(truth_df['transaction_id'].tolist()) if len(truth_df) else set()

        imei_accts: dict[str, set] = defaultdict(set)
        imei_sims: dict[str, set] = defaultdict(set)
        ip_accts: dict[str, set] = defaultdict(set)
        vel: dict[str, deque] = defaultdict(deque)
        last_loc: dict[str, tuple] = {}
        last_imei: dict[str, str] = {}
        last_sim: dict[str, str] = {}
        imei_changes: dict[str, deque] = defaultdict(lambda: deque(maxlen=10))
        sim_changes: dict[str, deque] = defaultdict(lambda: deque(maxlen=10))

        feat_age, feat_imei_a, feat_imei_s, feat_ip, feat_merch = [], [], [], [], []
        feat_vel, feat_dist, feat_dev, feat_simc, feat_hour = [], [], [], [], []

        for row in df.itertuples(index=False):
            ts = row.ts_dt
            acct = row.account_id
            opened_at = opened.get(acct, ts)
            feat_age.append(max(0.0, (ts - opened_at).total_seconds() / 86400.0))
            imei_accts[row.imei].add(acct)
            imei_sims[row.imei].add(row.sim_id)
            ip_accts[row.ip].add(acct)
            feat_imei_a.append(float(len(imei_accts[row.imei])))
            feat_imei_s.append(float(len(imei_sims[row.imei])))
            feat_ip.append(float(len(ip_accts[row.ip])))
            feat_merch.append(prior.get(row.merchant_id, 0.05))
            q = vel[acct]
            q.append(ts)
            cutoff = ts - pd.Timedelta(hours=1)
            while q and q[0] < cutoff:
                q.popleft()
            feat_vel.append(float(len(q)))
            xy = loc_xy.get(row.location_id)
            prev = last_loc.get(acct)
            if prev and xy:
                feat_dist.append(haversine_km(prev[0], prev[1], xy[0], xy[1]))
            else:
                feat_dist.append(0.0)
            if xy:
                last_loc[acct] = xy
            prev_i = last_imei.get(acct)
            imei_changes[acct].append(1.0 if prev_i and prev_i != row.imei else 0.0)
            last_imei[acct] = row.imei
            ch = imei_changes[acct]
            feat_dev.append(float(sum(ch) / max(1, len(ch))))
            prev_s = last_sim.get(acct)
            sim_changes[acct].append(1.0 if prev_s and prev_s != row.sim_id else 0.0)
            last_sim[acct] = row.sim_id
            chs = sim_changes[acct]
            feat_simc.append(float(sum(chs) / max(1, len(chs))))
            hour = int(ts.hour)
            feat_hour.append(abs(hour - typical.get(row.customer_id, 14)))

        df['account_age_days'] = feat_age
        df['imei_account_count'] = feat_imei_a
        df['imei_sim_count'] = feat_imei_s
        df['ip_account_count'] = feat_ip
        df['merchant_fraud_rate'] = feat_merch
        df['txn_velocity_1h'] = feat_vel
        df['location_distance_km'] = feat_dist
        df['device_change_rate'] = feat_dev
        df['sim_change_rate'] = feat_simc
        df['hour_deviation'] = feat_hour
        df['label'] = df['transaction_id'].map(lambda i: 1 if i in fraud_ids else 0).astype(int)
        drop = [c for c in df.columns if c in LEAKAGE_COLUMNS or c == 'ts_dt']
        df = df.drop(columns=drop, errors='ignore')
        return df


def build_dataset(n_transactions: int, seed: int = 42, persist: bool = False, output_root: Path | None = None) -> DatasetBundle:
    bundle = SyntheticFraudWorld(n_transactions, seed).build()
    if persist:
        persist_bundle(bundle, output_root)
    return bundle


def persist_bundle(bundle: DatasetBundle, output_root: Path | None = None):
    ensure_dirs()
    raw = Path(output_root) / 'raw' if output_root else RAW_DIR
    gen = Path(output_root) / 'generated' if output_root else GENERATED_DIR
    proc = Path(output_root) / 'processed' if output_root else PROCESSED_DIR
    for p in (raw, gen, proc):
        p.mkdir(parents=True, exist_ok=True)
    bundle.customers.to_csv(raw / 'customers.csv', index=False)
    bundle.accounts.to_csv(raw / 'accounts.csv', index=False)
    bundle.sims.to_csv(raw / 'sims.csv', index=False)
    bundle.handsets.to_csv(raw / 'handsets.csv', index=False)
    bundle.merchants.to_csv(raw / 'merchants.csv', index=False)
    bundle.networks.to_csv(raw / 'networks.csv', index=False)
    bundle.locations.to_csv(raw / 'locations.csv', index=False)
    bundle.transactions.to_csv(gen / 'transactions.csv', index=False)
    bundle.fraud_cases.to_csv(gen / 'fraud_cases.csv', index=False)
    bundle.fraud_patterns.to_csv(gen / 'fraud_patterns.csv', index=False)
    bundle.ground_truth.to_csv(gen / 'ground_truth.csv', index=False)
    (gen / 'generation_manifest.json').write_text(json.dumps(bundle.manifest, indent=2, default=str), encoding='utf-8')
    bundle.processed.to_csv(proc / 'transactions.csv', index=False)
    policy = {
        'target_columns': sorted(TARGET_COLUMNS),
        'feature_columns': FEATURES,
        'leakage_columns_excluded': sorted(LEAKAGE_COLUMNS),
        'notes': 'label is the supervised target only. fraud_reason and pattern identifiers live in generated/ground_truth.csv and must not be trained on.',
    }
    (proc / 'label_policy.json').write_text(json.dumps(policy, indent=2), encoding='utf-8')
