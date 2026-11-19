"""Independent boundary review: real request mapping, mutated trusted receipts."""
import copy
import pytest
from backend.reasoning_math import solve_math, validate_math_result


@pytest.mark.parametrize('text,status,key', [
    ('معكوس [[1,2],[2,4]]','no_solution','inverse'),
    ('القيم الذاتية [[2,1],[1,2]]','solved','eigenpairs'),
])
def test_advertised_matrix_text_preserves_exact_json_integers(text,status,key):
    answer=solve_math(text)
    assert answer['problem_status']==status
    assert key in answer['result']


@pytest.mark.parametrize('mutation',['missing','extra','changed_request','wrong_original','bad_status'])
def test_batch_receipt_requires_every_child_bound_to_its_request(mutation):
    text='احسب -2^2 و(-2)^2'
    answer=copy.deepcopy(solve_math(text))
    children=answer['result']['results']
    if mutation=='missing':children.pop()
    elif mutation=='extra':children.append(copy.deepcopy(children[0]))
    elif mutation=='changed_request':children[0]['request']['expression']='999'
    elif mutation=='wrong_original':children[0]['original_text']='other equation'
    elif mutation=='bad_status':children[0]['execution_status']='cancelled';children[0]['problem_status']='no_solution'
    with pytest.raises(ValueError):validate_math_result(answer,text)


def test_unrecognized_check_state_cannot_verify_a_solution():
    text='solve x=7';answer=solve_math(text)
    answer['verification'][0]['status']='not_actually_checked'
    with pytest.raises(ValueError):validate_math_result(answer,text)


def test_explicit_target_variable_survives_inferred_parameter_names():
    answer=solve_math('solve a*x=1; variable: a')
    assert answer['request']['variable']=='a'
    assert answer['result']['variable']=='a'
    assert answer['result']['cases'][0]['solutions']==['1/x']
