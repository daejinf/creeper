"""Lossless boundary conversions shared by B and C. Ambiguous values stay unresolved."""
def normalize_fact(key, value, members=()):
    if key in {'start_date','end_date'} and isinstance(value,str):
        import re
        dates=set(re.findall(r'20\d{2}-\d{2}-\d{2}',value))
        if len(dates)==1:return next(iter(dates))
    if key == "frequency_per_week" and isinstance(value, str):
        import re
        match = re.fullmatch(r"\s*(?:주\s*)?(\d+)\s*(?:회|번)?\s*", value)
        if match:return int(match[1])
    if key == "roles" and isinstance(value, list):
        roles = {}
        for row in value:
            if not isinstance(row, dict) or set(row)-{"member_id","role"} or not isinstance(row.get("member_id"),str) or not isinstance(row.get("role"),str):
                return value  # Unknown/ambiguous shapes must still fail review.
            if row["member_id"] in roles and roles[row["member_id"]] != row["role"]:
                return value
            roles[row["member_id"]] = row["role"]
        return roles
    if key == "roles" and isinstance(value, str) and members:
        names = {m.nickname: m.member_id for m in members}
        pairs = [part.strip().split(":", 1) for part in value.split(",")]
        if pairs and all(len(pair)==2 and pair[0].strip() in names and pair[1].strip() for pair in pairs):
            return {names[pair[0].strip()]: pair[1].strip() for pair in pairs}
    if key == "roles" and isinstance(value, dict) and members:
        names = {m.nickname: m.member_id for m in members}
        return {names.get(k,k):v for k,v in value.items()}
    if key == "milestones" and isinstance(value, list):
        output=[]
        for item in value:
            if isinstance(item,str):item={"title":item}
            elif isinstance(item,dict):
                item=dict(item)
                for old,new in [("name","title"),("date","due_date"),("target_date","due_date")]:
                    if old in item and new not in item:item[new]=item.pop(old)
            output.append(item)
        return output
    if key in {"completion_criteria", "checkins"} and isinstance(value,str):
        return [value] if value.strip() else []
    if key == "privacy_scope" and isinstance(value,str):
        if value in {"OBJECTIVE_ONLY", "PRIVATE", "CUSTOM"}:return value
        if any(x in value for x in ["미정", "더 논의", "정하지 않"]):return None
        if "완료 여부" in value and ("만" in value or "달성률" in value):return "OBJECTIVE_ONLY"
        if value.strip() in {"본인만", "본인만 확인", "비공개", "개인만 확인"}:return "PRIVATE"
    if key == "checkins" and isinstance(value,dict):
        value=[value]
    if key == "checkins" and isinstance(value,list):
        output=[]
        for item in value:
            if isinstance(item,dict) and item and set(item)<={"weekday","day","time","method","frequency","content"} and all(isinstance(v,str) for v in item.values()):
                output.append(" · ".join(item.values()))
            else:output.append(item)
        return output
    return value
