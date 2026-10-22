import ast

import pytest

from clarifytrial.preparation.leaf_reference import parse_reference,read_reference,structural_profile


def test_headers_do_not_require_blank_lines_and_preserve_original_strings():
    reference=read_reference("\ufeff'INC'\n\n'Patient\'s age'\n\n'age()'\nage().num_filter(eq(op(GT),val(\"18\")))")
    assert reference.raw_text=="Patient's age"
    assert structural_profile(parse_reference(reference.logical_form))["numeric_filters"]==1


def test_quoted_text_is_not_changed_when_reserved_call_tokens_are_parsed():
    expression='drug("for(and( or(").for(cond("x")).except(drug("y"))'
    tree=parse_reference(expression)
    assert {n.value for n in ast.walk(tree) if isinstance(n,ast.Constant)}=={'for(and( or(', 'x', 'y'}
    assert structural_profile(tree)["methods"]==["except_","for_"]
    assert structural_profile(parse_reference('or(cond("a"),cond("b"))'))["functions"]==["cond","or_"]


@pytest.mark.parametrize("expression",["", 'cond("one")\n\ncond("two")', 'neg(proc("x"),.temporality(eq(val("3"))))', 'cond("x"'])
def test_missing_or_multiple_or_malformed_forms_are_not_truncated(expression):
    with pytest.raises(ValueError): parse_reference(expression)


def test_external_data_is_only_parsed_and_context_is_not_silently_discarded():
    # An unsafe-looking expression is data in an AST; no eval or import occurs.
    tree=parse_reference('__import__("os").system("not executed")')
    assert not structural_profile(tree)["restricted_numeric_candidate"]
    context=parse_reference('lab("x").num_filter(eq(op(GT),val("3"))).temporality(eq(val("1")))')
    assert not structural_profile(context)["restricted_numeric_candidate"]
    assert structural_profile(context)["numeric_filters"]==1
