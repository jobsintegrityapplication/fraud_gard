REQUIRED_COLUMNS = (
    'transaction_id',
    'customer_id',
    'account_id',
    'sim_id',
    'imei',
    'merchant_id',
    'amount',
    'ts',
)

OPTIONAL_COLUMNS = (
    'ip',
    'location',
    'network_id',
    'handset_id',
    'label',
    'channel',
    'currency',
)

COLUMN_ALIASES = {
    'id': 'transaction_id',
    'txn_id': 'transaction_id',
    'location_code': 'location',
}

STATUSES = ('PENDING', 'VALIDATING', 'PROCESSING', 'COMPLETED', 'FAILED')
SUPPORTED_FORMATS = ('csv', 'json', 'parquet')
MAX_BYTES = 50 * 1024 * 1024
IMEI_LEN = 15
AMOUNT_MAX = 10_000_000
