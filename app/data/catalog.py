"""Columns that must never be used as model features."""

LEAKAGE_COLUMNS = frozenset({
    'fraud_reason',
    'pattern_code',
    'pattern_name',
    'pattern_id',
    'injection_id',
    'case_id',
    'fraud_case_id',
    'is_injected',
    'is_fraud_injected',
    'ground_truth',
    'label_source',
    'injected_pattern',
})

# Binary class target only — not a feature.
TARGET_COLUMNS = frozenset({'label'})

SIZE_PRESETS = (10_000, 50_000, 100_000, 1_000_000)

FRAUD_PATTERNS = (
    ('FP-001', 'SHARED_IMEI_ACCOUNTS', 'Multiple accounts sharing one IMEI'),
    ('FP-002', 'SHARED_IMEI_SIMS', 'Multiple SIMs using one IMEI'),
    ('FP-003', 'RAPID_SIM_SWITCH', 'Rapid SIM switching on one account/device'),
    ('FP-004', 'RAPID_DEVICE_SWITCH', 'Rapid device switching on one account/SIM'),
    ('FP-005', 'SHARED_IP_ACCOUNTS', 'Multiple accounts sharing one IP'),
    ('FP-006', 'ABNORMAL_VELOCITY', 'Abnormal transaction velocity'),
    ('FP-007', 'ABNORMAL_AMOUNT', 'Abnormal transaction amount'),
    ('FP-008', 'IMPOSSIBLE_TRAVEL', 'Impossible or rapid location movement'),
    ('FP-009', 'SUSPICIOUS_MERCHANT', 'Suspicious merchant collusion'),
    ('FP-010', 'COORDINATED_RING', 'Coordinated fraud ring'),
)

CITIES = (
    ('BLR', 'Bengaluru', 12.9716, 77.5946, 'IN'),
    ('MUM', 'Mumbai', 19.0760, 72.8777, 'IN'),
    ('DEL', 'Delhi', 28.6139, 77.2090, 'IN'),
    ('HYD', 'Hyderabad', 17.3850, 78.4867, 'IN'),
    ('CHN', 'Chennai', 13.0827, 80.2707, 'IN'),
    ('KOL', 'Kolkata', 22.5726, 88.3639, 'IN'),
    ('PUN', 'Pune', 18.5204, 73.8567, 'IN'),
    ('AMD', 'Ahmedabad', 23.0225, 72.5714, 'IN'),
    ('JAI', 'Jaipur', 26.9124, 75.7873, 'IN'),
    ('KOZ', 'Kochi', 9.9312, 76.2673, 'IN'),
    ('GOA', 'Goa', 15.2993, 74.1240, 'IN'),
    ('LKO', 'Lucknow', 26.8467, 80.9462, 'IN'),
    ('SIN', 'Singapore', 1.3521, 103.8198, 'SG'),
    ('DXB', 'Dubai', 25.2048, 55.2708, 'AE'),
    ('LON', 'London', 51.5074, -0.1278, 'GB'),
    ('NYC', 'New York', 40.7128, -74.0060, 'US'),
    ('SFO', 'San Francisco', 37.7749, -122.4194, 'US'),
    ('NBO', 'Nairobi', -1.2921, 36.8219, 'KE'),
)

MANUFACTURERS = (
    ('35', 'Samsung'),
    ('01', 'Apple'),
    ('86', 'Xiaomi'),
    ('49', 'OnePlus'),
    ('91', 'Vivo'),
    ('13', 'Oppo'),
    ('04', 'Google'),
    ('35', 'Motorola'),
)

MCC = (
    ('MGROC', 'grocery', 4.8, 0.55),
    ('MFUEL', 'fuel', 5.1, 0.40),
    ('MUTIL', 'utilities', 6.2, 0.35),
    ('MECOM', 'ecommerce', 5.6, 0.70),
    ('MTRAV', 'travel', 7.4, 0.80),
    ('MELEC', 'electronics', 8.1, 0.85),
    ('MGIFT', 'gift_cards', 6.8, 0.90),
    ('MCRYP', 'crypto_otc', 7.9, 1.05),
    ('MGAMB', 'gaming', 5.4, 0.75),
    ('MREST', 'restaurant', 4.6, 0.50),
)
