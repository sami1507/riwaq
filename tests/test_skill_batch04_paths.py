"""Independent product-entry coverage for the deterministic planning adapters."""
import pytest
from backend import skill_core
from test_app import client
from test_skills import pid,submit

def instant(value):return {'kind':'instant','value':'2026-11-17T'+value+':00Z'}
def window(id,start,end):return {'id':id,'start':instant(start),'end':instant(end)}

CASES=[
 ('weekly-planning',{'operation':'weekly','startDate':'2026-11-16','weekStartsOn':0,'windows':[window('day','09:00','12:00')],'tasks':[{'id':'a','title':'Weekly independent','minutes':60}]},'scheduled'),
 ('shift-schedule-planning',{'operation':'shift','windows':[window('day','09:00','23:00')],'shifts':[{'id':'s1','startDate':'2026-11-17','startTime':'18:00','endTime':'01:00','endNextDay':True,'commuteBeforeMinutes':30,'commuteAfterMinutes':15,'restAfterMinutes':420}],'tasks':[{'id':'a','title':'Before shift','minutes':60}]},'scheduled'),
 ('calendar-conflict-check',{'operation':'conflicts','windows':[window('candidate','09:00','12:00')],'commitments':[dict(window('one','10:00','11:00'),title='One'),dict(window('two','10:30','11:30'),title='Two')]},'conflicts'),
 ('timezone-scheduling',{'operation':'timezone','times':[instant('23:30')],'displayZones':['Asia/Tokyo','America/New_York']},'normalized_times'),
 ('time-blocking',{'operation':'time-blocking','windows':[window('day','09:00','12:00')],'tasks':[{'id':'a','title':'Continuous focus','minutes':60}]},'scheduled'),
 ('meeting-time-search',{'operation':'meeting-search','durationMinutes':30,'participants':[{'id':'a','zone':'Asia/Jerusalem','windows':[window('wa','09:00','11:00')]},{'id':'b','zone':'America/New_York','windows':[window('wb','10:00','12:00')]},{'id':'c','zone':'Asia/Tokyo','windows':[window('wc','09:30','10:30')]}]},'candidates'),
 ('contingency-scheduling',{'operation':'replan','windows':[window('day','09:00','12:00')],'tasks':[{'id':'done','title':'Done','minutes':30,'completed':True},{'id':'a','title':'Remaining','minutes':60}],'scenario':{'reason':'Synthetic interruption','unavailable':[window('delay','09:00','10:00')]}},'scheduled'),
 ('meeting-agenda',{'operation':'agenda','agenda':{'title':'30 minutes','durationMinutes':30,'items':[{'id':'decision','title':'Decide scope','minutes':20},{'id':'discussion','title':'Discuss detail','minutes':15,'dependsOn':['decision']}]}},'deferred'),
]

@pytest.mark.parametrize('slug,options,key',CASES)
def test_each_planning_adapter_through_actual_product_task(client,slug,options,key):
    p=pid(client)
    result=submit(client,p,slug,{'planningOperation':{'zone':'UTC',**options}},request='Prepare the requested reviewed proposal',key='adapter-'+slug)
    assert result['status'] in {'completed','partial'},result
    output=result['result']['structured'][slug]
    assert output[key],output
    assert output['events_created']==0 and output['messages_sent']==0 and output['reminders_created']==0
    if slug=='meeting-time-search':
        assert len(output['candidates'])==1
        assert '10:00' in str(output['candidates'][0]) and '10:30' in str(output['candidates'][0])
    if slug=='meeting-agenda':assert output['allocated_minutes']==20


@pytest.mark.parametrize('prompt,slug',[
 ('خطط يومي','daily-planning'),('Plan my week','weekly-planning'),('תכנן שבוע','weekly-planning'),
 ('ذكرني','reminder-creation'),('Remind me','reminder-creation'),('הזכר לי','reminder-creation'),
 ('اكتب مسودة بريد','email-drafting'),('Draft email','email-drafting'),('כתוב טיוטה','email-drafting'),
 ('محضر الاجتماع','meeting-minutes'),('meeting minutes','meeting-minutes'),('פרוטוקול','meeting-minutes'),
])
def test_bounded_multilingual_workflow_selection(prompt,slug):
    assert slug in skill_core.select_skills(skill_core.classify(prompt))


def test_catalog_preserves_original_seventy_five_and_loads_selected_only():
    import json
    from pathlib import Path
    from backend.skill_loading import load_packages
    before=json.loads(Path('reports/skills-batch-04-20261116/baseline/config/skills-batch-01.json').read_text('utf-8'))
    current=skill_core.catalog();assert current['skills'][:75]==before['skills']
    assert [r['number'] for r in current['skills'] if r['number']<=100]==list(range(1,101))
    for item in current['skills'][75:100]:
        loaded=load_packages([item['slug']],current,skill_core.ROOT)
        assert len(loaded['loaded'])==1 and not loaded['executed_scripts'] and loaded['characters']<3000
