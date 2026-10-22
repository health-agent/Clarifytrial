from itertools import product

import pytest

from clarifytrial.preparation.leaf_numeric_reference import compile_numeric_reference,UnsupportedLeafReference


def truth(formula, values):
    op=formula[0]
    if op=="and":return all(truth(c,values) for c in formula[1:])
    if op=="or":return any(truth(c,values) for c in formula[1:])
    if op=="not":return not truth(formula[1],values)
    value,threshold=values[formula[1]],formula[2]
    return {"gt":value>threshold,"gte":value>=threshold,"lt":value<threshold,"lte":value<=threshold,"eq":value==threshold}[op]


def test_between_retains_four_endpoint_conventions_and_original_units():
    r=compile_numeric_reference('age().num_filter(eq(val("18"),op(BETWEEN),val("50"),temporal_unit(YEAR)))')
    assert r["facts"][0]["label"]=="age" and r["facts"][0]["unit"]=="years"
    assert len(r["reference_alternatives"])==4
    for value,expected in ((17,{False}),(18,{False,True}),(30,{True}),(50,{False,True}),(51,{False})):
        assert {truth(f,{"f0":value}) for f in r["reference_alternatives"]}==expected


def test_composite_negation_and_independent_measurements_are_preserved():
    r=compile_numeric_reference('neg(union(lab("A").num_filter(eq(op(GT),val("3"),unit("mg"),unit("dL"))),'
        'lab("B").num_filter(eq(op(LTEQ),val("5"),unit("mg"),unit("dL")))))')
    assert r["facts"][0]["unit"]=="mg/dL"
    for a,b in product((2,3,4),(4,5,6)):
        assert truth(r["formula"],{"f0":a,"f1":b})== (not (a>3 or b<=5))


def test_explicit_annotation_alias_preserves_the_numeric_definition():
    r=compile_numeric_reference('cond("obesity").equiv(vital("body mass index").equiv(vital("BMI"))'
        '.num_filter(eq(op(GT),val("30"),unit("kg"),unit("m 2"))))')
    assert r["facts"][0]["label"]=="body mass index"
    assert r["facts"][0]["unit"]=="kg/m2"
    assert r["formula"]==["gt","f0",30.]


@pytest.mark.parametrize("expression",[
    'intersect(cond("kidney disease"),lab("A").num_filter(eq(op(LT),val("3"),unit("mg"))))',
    'lab("A").num_filter(eq(op(LT),val("3"),unit("mg"))).temporality(eq(val("1")))',
    'lab("A").num_filter(eq(op(LT),val("3")))',
    'clin_score("ASA").num_filter(eq(val("II")))',
    'lab("A").num_filter(eq(op(LT),val("0,5"),unit("mg")))',
    'cond("a").temporality(eq(val("1"))).equiv(lab("A").num_filter(eq(op(LT),val("3"),unit("mg"))))',
    'lab("A").num_filter(eq(op(LT),val("3"),unit("mg"))).equiv(lab("B").num_filter(eq(op(LT),val("4"),unit("mg"))))',
])
def test_unrepresented_context_values_or_units_are_not_deleted(expression):
    with pytest.raises(UnsupportedLeafReference):compile_numeric_reference(expression)


def test_annotation_endpoint_ambiguity_is_not_patient_information_unknown():
    from pathlib import Path
    import runpy
    expected=runpy.run_path(str(Path(__file__).resolve().parents[1]/"scripts/run_natural_protocol_logic_research.py"))["expected"]
    ref=compile_numeric_reference('age().num_filter(eq(op(BETWEEN),val("18"),val("50"),temporal_unit(YEAR)))')
    case={**ref,"kind":"inclusion","domains":{"f0":[17,18,30,50,51]}}
    assert expected(case,{"f0":18}) is None
    assert expected(case,{"f0":None})=="not_confirmed"
    assert expected(case,{"f0":30})=="confirmed"
    assert expected(case,{"f0":51})=="ineligible"
