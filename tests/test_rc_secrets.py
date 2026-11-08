from deploy.secret_scan import inspect


def test_candidate_value_is_never_returned():
    value=b'sk-or-v1-'+b'x'*64
    result=inspect([('fixture.py',value)])
    assert result['status']=='fail' and value.decode() not in str(result)
    assert result['historical_revocation'].endswith('not_performed')
    path_result=inspect([(value.decode()+'.txt',b'fixture')]);assert path_result['status']=='fail' and value.decode() not in str(path_result)


def test_placeholder_is_not_reported_as_a_usable_key():
    assert inspect([('example',b'OPENROUTER_API_KEY_FILE=/run/secrets/openrouter')])['status']=='pass'
