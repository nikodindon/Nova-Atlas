"""Opt-in PostgreSQL snapshot tools. Business producers remain legacy-only."""


class StorageError(ValueError):
    """Developer-controlled reason codes only; no driver/record/credential text."""


def runtime_mode(config):
    mode=config.get('storage',{}).get('mode','legacy')
    if mode not in ('legacy','postgres'):raise StorageError('invalid_storage_mode')
    if mode=='postgres':raise StorageError('postgres_business_cutover_not_implemented_use_snapshot_cli')
    return mode
