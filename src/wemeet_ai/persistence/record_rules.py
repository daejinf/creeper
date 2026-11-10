"""Shared storage rules, with no implicit storage selection or fallback."""
def scope_for(module,payload):
    if module.startswith('E.'):
        owner=payload.get('owner_member_id')
        if not owner:raise ValueError('개인 대상 ID가 필요합니다.')
        return 'PRIVATE',owner
    if module.startswith('F'):return 'SERVICE',''
    if set(payload)&{'owner_member_id','private_notes','member_profile','member_profiles','private_conversation'}:
        raise ValueError('개인 자료를 공개 모듈 입력으로 저장할 수 없습니다.')
    return 'PUBLIC',''
