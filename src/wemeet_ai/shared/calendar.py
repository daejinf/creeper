"""Validate generated weekday labels without changing confirmed dates."""
import re
from datetime import date

def remove_wrong_weekdays(text, reference_date):
    changed=[]
    def replace(match):
        year=int(match['year']) if match['year'] else reference_date.year
        try: actual=date(year,int(match['month']),int(match['day'])).weekday()
        except ValueError:return match[0]
        if '월화수목금토일'[actual]!=match['weekday']:
            changed.append(match[0])
            return match[0][:match[0].rfind('(')]
        return match[0]
    result=re.sub(r'(?:(?P<year>20\d{2})[-/])?(?P<month>\d{1,2})[-/](?P<day>\d{1,2})\s*\((?P<weekday>[월화수목금토일])(?:요일)?\)',replace,text)
    result=re.sub(r'(?:(?P<year>20\d{2})년\s*)?(?P<month>\d{1,2})월\s*(?P<day>\d{1,2})일\s*\((?P<weekday>[월화수목금토일])(?:요일)?\)',replace,result)
    return result,changed
