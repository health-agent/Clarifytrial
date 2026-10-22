"""Read public Leaf logical-form annotations as data, never executable code.

This adapter inventories external annotation structure. It does not convert the
corpus into clinical gold, drop contextual methods, or repair annotator semantics.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import io
import re
import tokenize


@dataclass(frozen=True)
class LeafReference:
    kind: str
    raw_text: str
    augmented_text: str
    logical_form: str


def read_reference(text: str) -> LeafReference:
    """Read three quoted header lines, accepting variable blank-line spacing.

    Header strings are original data, not Python literals. Preserve apostrophes
    and backslashes rather than evaluating them or normalizing their meaning.
    """
    lines=text.lstrip("\ufeff").splitlines()
    headers=[]
    position=0
    while position < len(lines) and len(headers) < 3:
        line=lines[position].strip()
        position += 1
        if not line:
            continue
        match=re.fullmatch(r"(['\"])(.*)\1",line)
        if not match:
            raise ValueError("expected three complete quoted Leaf header lines")
        headers.append(match.group(2))
    if len(headers) != 3 or headers[0] not in {"INC", "EXC"}:
        raise ValueError("missing or invalid Leaf criterion headers")
    return LeafReference(headers[0], headers[1], headers[2], "\n".join(lines[position:]).strip())


def parse_reference(expression: str) -> ast.expr:
    """Parse the function-chain dialect without eval/exec or importing the data.

    Rename only call tokens that are Python keywords; quoted text is untouched.
    Multiple top-level expressions remain unsupported and are never silently
    truncated to the first one. AST parsing does not establish semantic validity.
    """
    if not expression.strip():
        raise ValueError("annotation has no logical form")
    try:
        tokens=list(tokenize.generate_tokens(io.StringIO("("+expression+"\n)").readline))
    except tokenize.TokenError as error:
        raise ValueError("unsupported or malformed annotation tokens") from error
    reserved={"and", "or", "for", "except"}
    for i, token in enumerate(tokens):
        if token.type != tokenize.NAME or token.string not in reserved:
            continue
        following=next((other for other in tokens[i+1:] if other.type not in {
            tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.COMMENT}),None)
        if following is not None and following.string == "(":
            tokens[i]=token._replace(string=token.string+"_")
    try:
        return ast.parse(tokenize.untokenize(tokens), mode="eval").body
    except (SyntaxError, IndentationError) as error:
        raise ValueError("unsupported or malformed annotation syntax") from error


def structural_profile(tree: ast.expr) -> dict:
    """Describe a bounded candidate profile, without claiming executable meaning."""
    functions=[node.func.id for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Name)]
    methods=[node.func.attr for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)]
    numeric=methods.count("num_filter")
    allowed_functions={"lab","vital","age","clin_score","measurement","obs","cond","eq","val","op",
        "unit","per","temporal_unit","union","intersect","neg"}
    return {"functions":sorted(set(functions)), "methods":sorted(set(methods)),
        "numeric_filters":numeric, "logical_operators":sorted(set(functions) & {"union","intersect","neg"}),
        "restricted_numeric_candidate":bool(numeric and set(methods) <= {"num_filter","equiv"}
            and set(functions) <= allowed_functions),
        "canonical_syntax":ast.dump(tree, include_attributes=False)}
