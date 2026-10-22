"""Bounded numeric interpretation of externally authored Leaf annotations.

Research scoring only, not a clinical source compiler. Unsupported context is
rejected as a whole; BETWEEN endpoint conventions remain explicit alternatives.
No external code is executed and no corpus annotation is repaired from raw text.
"""
from __future__ import annotations

import ast
from itertools import product
import math
import re

from .leaf_reference import parse_reference


class UnsupportedLeafReference(ValueError):
    pass


def _call(node, name):
    return isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id == name


def _literal(node):
    if isinstance(node,ast.Constant) and isinstance(node.value,(str,int,float)):
        return str(node.value)
    if isinstance(node,ast.Name):
        return node.id
    raise UnsupportedLeafReference("nonliteral comparison argument")


def _comparison(node, entity_type):
    if not _call(node,"eq") or node.keywords:
        raise UnsupportedLeafReference("numeric filter is not one eq expression")
    values=[]; operators=[]; units=[]; per=[]
    for arg in node.args:
        if not isinstance(arg,ast.Call) or not isinstance(arg.func,ast.Name) or len(arg.args)!=1 or arg.keywords:
            raise UnsupportedLeafReference("unsupported comparison argument")
        name=arg.func.id; value=_literal(arg.args[0])
        if name=="val":
            if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)",value):
                raise UnsupportedLeafReference("nonnumeric or ambiguous numeric notation")
            try: number=float(value)
            except ValueError as error: raise UnsupportedLeafReference("nonnumeric value or Roman category") from error
            if not math.isfinite(number): raise UnsupportedLeafReference("nonfinite threshold")
            values.append(number)
        elif name=="op":operators.append(value)
        elif name in {"unit","temporal_unit"}:units.append(value)
        elif name=="per":per.append(value)
        else:raise UnsupportedLeafReference("temporal or contextual comparison")
    if len(operators)>1 or not values:
        raise UnsupportedLeafReference("missing value or multiple comparison operators")
    # Units are annotation-derived. Repeated endpoint units describe one unit;
    # dimensionless clinical scores are the only explicit no-unit convention.
    unique=list(dict.fromkeys(units))
    unit="/".join(unique + per)
    unit={"YEAR":"years","MONTH":"months","WEEK":"weeks","DAY":"days","HOUR":"hours",
          "REF_RANGE_NORMAL":"xULN","ml/MINUTE":"mL/min","mL/MINUTE":"mL/min"}.get(unit,unit)
    unit=re.sub(r"\s+","",unit)
    if not unit:
        if entity_type=="clin_score":unit="score"
        else:raise UnsupportedLeafReference("annotation omits measurement unit")
    op=operators[0] if operators else "EQ"
    mapping={"GT":"gt","GTEQ":"gte","GTE":"gte","LT":"lt","LTEQ":"lte","LTE":"lte","EQ":"eq"}
    if op=="BETWEEN" and len(values)==2 and values[0]<values[1]:
        # Leaf's generic BETWEEN does not specify each endpoint's strictness.
        return [("range",values,unit)]
    if op not in mapping or len(values)!=1:
        raise UnsupportedLeafReference("unsupported operator/value cardinality")
    return [(mapping[op],values[0],unit)]


def _entity_type(node):
    while isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=="equiv":
        node=node.func.value
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Name):
        return node.func.id
    return ""


def _bare_equivalent(node):
    if not isinstance(node,ast.Call) or node.keywords:return False
    if isinstance(node.func,ast.Attribute):
        return node.func.attr=="equiv" and _bare_equivalent(node.func.value) and all(_bare_equivalent(n) for n in node.args)
    return (isinstance(node.func,ast.Name) and node.func.id in {
        "lab","vital","age","clin_score","measurement","obs","cond"}
        and all(isinstance(a,ast.Constant) for a in node.args))


def _compile(node, comparison=None):
    if not isinstance(node,ast.Call) or node.keywords:
        raise UnsupportedLeafReference("not a supported annotation call")
    if isinstance(node.func,ast.Attribute):
        method=node.func.attr
        if method=="num_filter" and comparison is None and len(node.args)==1:
            spec=_comparison(node.args[0],_entity_type(node.func.value))
            return _compile(node.func.value,spec)
        if method=="equiv":
            if comparison is not None:
                # Same annotated entity has an explicit abbreviation/example.
                # Validate that alternatives are names, not dropped constraints.
                for alias in node.args:
                    if not (isinstance(alias,ast.Call) and isinstance(alias.func,ast.Name)
                            and alias.func.id in {"lab","vital","age","clin_score","measurement","obs","cond"}
                            and all(isinstance(a,ast.Constant) for a in alias.args) and not alias.keywords):
                        raise UnsupportedLeafReference("nontrivial equivalent under a numeric filter")
                return _compile(node.func.value,comparison)
            candidates=[]
            for candidate in (node.func.value,*node.args):
                try:candidates.append(_compile(candidate))
                except UnsupportedLeafReference:
                    if not _bare_equivalent(candidate):
                        raise UnsupportedLeafReference("unrepresented equivalent cannot be discarded")
            if len(candidates)!=1:
                raise UnsupportedLeafReference("equivalence lacks one complete numeric definition")
            return candidates[0]
        raise UnsupportedLeafReference("unsupported method or nested numeric filter: "+method)
    if not isinstance(node.func,ast.Name):
        raise UnsupportedLeafReference("unsupported function target")
    name=node.func.id
    if name in {"union","intersect"}:
        if len(node.args)<2:raise UnsupportedLeafReference("logical node needs at least two children")
        return ["or" if name=="union" else "and",*[_compile(arg,comparison) for arg in node.args]]
    if name=="neg" and comparison is None and len(node.args)==1:
        return ["not",_compile(node.args[0])]
    if comparison is None:
        raise UnsupportedLeafReference("unrepresented nonnumeric conjunct or disjunct")
    if name not in {"lab","vital","age","clin_score","measurement","obs"}:
        raise UnsupportedLeafReference("numeric filter on unsupported entity")
    if name=="age" and not node.args:label="age"
    elif len(node.args)==1 and isinstance(node.args[0],ast.Constant) and isinstance(node.args[0].value,str):label=node.args[0].value
    else:raise UnsupportedLeafReference("missing or nonliteral measured entity")
    op,threshold,unit=comparison[0]
    return ["numeric",label,op,threshold,unit]


def compile_numeric_reference(expression: str) -> dict:
    """Return formula alternatives and annotation labels without lexical repair."""
    try: tree=_compile(parse_reference(expression))
    except ValueError as error:raise UnsupportedLeafReference(str(error)) from error
    facts={}; atoms=[]; ranges=[]

    def bind(node):
        if node[0]!="numeric":return [node[0],*[bind(child) for child in node[1:]]]
        _,label,op,threshold,unit=node
        identity=("".join(c for c in label.casefold() if c.isalnum()),unit.casefold())
        if identity not in facts:facts[identity]={"code":f"f{len(facts)}","label":label,"unit":unit,"thresholds":[]}
        fact=facts[identity];code=fact["code"]
        if op=="range":
            low,high=threshold;index=len(ranges);ranges.append((code,low,high))
            fact["thresholds"].extend((low,high))
            atoms.extend(((code,"gte",low),(code,"lte",high)))
            return ["range",index,code,low,high]
        fact["thresholds"].append(threshold);atoms.append((code,op,threshold))
        return [op,code,threshold]

    bound=bind(tree)
    if len(ranges)>3:raise UnsupportedLeafReference("too many ambiguous ranges for this probe")

    def instantiate(node, choices):
        if node[0]=="range":
            _,i,code,low,high=node;left,right=choices[i]
            return ["and",[left,code,low],[right,code,high]]
        if node[0] in {"and","or","not"}:return [node[0],*[instantiate(child,choices) for child in node[1:]]]
        return node

    alternatives=[instantiate(bound,choices) for choices in product((("gte","lte"),("gt","lte"),("gte","lt"),("gt","lt")),repeat=len(ranges))]
    return {"formula":alternatives[0],"reference_alternatives":alternatives,"facts":list(facts.values()),
        "atoms":atoms,"ambiguous_range_count":len(ranges),"numeric_atom_count":len(atoms)}
