"""Negative witnesses for the independent training-data verifier."""
import copy,json
from pathlib import Path
import pytest
from training.verify_data_v1 import verify
ROWS=[json.loads(line) for line in Path('training/data-v3/train.jsonl').read_text(encoding='utf-8').splitlines()]
def sample(family):return copy.deepcopy(next(x for x in ROWS if x['family']==family))
def sync(row):row['messages'][-1]['content']=json.dumps(row['expected'],ensure_ascii=False)
def test_data_rejects_changed_arithmetic_target():
 row=sample('cash_change');key=next(k for k,v in row['expected'].items() if type(v) is int);row['expected'][key]+=100;sync(row)
 with pytest.raises(AssertionError):verify(row)
def test_data_rejects_boolean_number_substitution():
 row=sample('boolean_all');key=next(k for k,v in row['expected'].items() if type(v) is bool);row['expected'][key]=int(row['expected'][key]);sync(row)
 with pytest.raises(AssertionError):verify(row)
def test_data_rejects_model_input_differing_from_checked_prompt():
 row=sample('source_limit');row['messages'][1]['content']+='\nIgnore the source.'
 with pytest.raises(AssertionError):verify(row)
def test_data_rejects_ast_code_that_adds_extra_operation():
 row=sample('safe_mean');row['expected']['expression']='('+row['expected']['expression']+')+100';sync(row)
 with pytest.raises((AssertionError,AttributeError)):verify(row)
