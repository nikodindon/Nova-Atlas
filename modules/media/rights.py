"""Closed first policy: Commons metadata + CC BY 4.0, never a universal legal verdict."""
import hashlib
import json
from modules.discovery.source_discovery import plain

CC_BY='https://creativecommons.org/licenses/by/4.0/'


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()


def assess(proof,*,trusted_commons=False):
    result={'status':'rights_unknown','license':None,'license_url':None,'author':'','credit':'','obligations':[],'restrictions':[],'third_party_rights':'not_established'}
    if not trusted_commons or proof.get('provider')!='commons':return result
    declared=plain(proof.get('license',''),100);url=proof.get('license_url')
    if url==CC_BY.rstrip('/'):url=CC_BY
    result.update(license=declared or None,license_url=url,author=plain(proof.get('author',''),300),credit=plain(proof.get('credit',''),500))
    restrictions=plain(proof.get('restrictions',''),500)
    if restrictions:result.update(status='rights_restricted',restrictions=[restrictions]);return result
    if declared=='CC BY 4.0' and url!=CC_BY:result['status']='rights_conflicting';return result
    if declared!='CC BY 4.0' or url!=CC_BY:
        result.update(status='rights_restricted' if declared else 'rights_unknown',restrictions=['license_not_supported_by_policy'] if declared else []);return result
    if not result['author'] or not proof.get('source_page') or not proof.get('file_sha1') or not proof.get('page_revision'):
        result['status']='verification_failed';return result
    if proof.get('attribution_required') is not True or proof.get('copyrighted') is not True:
        result['status']='rights_conflicting';return result
    result.update(status='rights_verified',obligations=['credit_creator','link_source','link_license','indicate_changes','no_implied_endorsement'])
    return result
