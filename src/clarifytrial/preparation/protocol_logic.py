"""Compile cited source relations into eligibility-facing logic.

This verifies a bounded English clause grammar, not arbitrary clinical prose.
Unknown qualifiers or ambiguous scope remain unsupported. A model's proposed
tree is never treated as semantic proof just because its JSON references exist.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from ..contracts import CriterionKind,CriterionLogic,CriterionLogicOperator,TrialCriterion
from .structured_value_validation import _CONCEPT_ALIASES


class UnsupportedSourceLogic(ValueError):
    pass


@dataclass(frozen=True)
class GroundedLogicAtom:
    criterion: TrialCriterion
    start: int
    end: int


@dataclass(frozen=True)
class GroundedLogicProposal:
    start: int
    end: int
    predicate: CriterionLogic


@dataclass(frozen=True)
class CompiledSourceLogic:
    eligibility_logic: CriterionLogic | None
    verified_spans: tuple[tuple[int,int],...]
    issues: tuple[str,...]
    records: tuple[dict,...]


def _node(operator,children,minimum=None):
    return CriterionLogic(operator=operator,children=children,minimum_required=minimum)


def _leaf(criterion_id):
    return CriterionLogic(operator="criterion",criterion_id=criterion_id)


def eligibility_from_membership(node: CriterionLogic,kind: CriterionKind) -> CriterionLogic:
    """An exclusion of k of n predicates permits n-k+1 nonmembers."""
    if node.operator is CriterionLogicOperator.CRITERION:
        return node
    operator=node.operator.value
    minimum=node.minimum_required
    if kind is CriterionKind.EXCLUSION:
        if operator=="all":operator="any"
        elif operator=="any":operator="all"
        else:minimum=len(node.children)-minimum+1
    return _node(operator,[eligibility_from_membership(c,kind) for c in node.children],minimum)


def _canonical(node):
    if node.operator is CriterionLogicOperator.CRITERION:
        return ("criterion",node.criterion_id)
    if node.operator is CriterionLogicOperator.AT_LEAST and node.minimum_required in {1,len(node.children)}:
        return _canonical(_node("any" if node.minimum_required==1 else "all",node.children))
    values=[]
    for child in node.children:
        if child.operator==node.operator and node.operator in {CriterionLogicOperator.ALL,CriterionLogicOperator.ANY}:
            values.extend(_canonical(c) for c in child.children)
        else:values.append(_canonical(child))
    return (node.operator.value,node.minimum_required,tuple(sorted(values,key=repr)))


def _normalized_symbols(text):
    return unicodedata.normalize("NFKC",text).casefold().replace("\\","").replace("≥",">=").replace("≤","<=").replace("=<","<=")


def _range_padding(text,concept,unit):
    text=_normalized_symbols(text)
    labels={concept.replace("_"," "),*_CONCEPT_ALIASES.get(concept,())}
    if "ecog" in concept.casefold():labels.update({"ecog","ecog performance status","ecog performance status score"})
    if concept.casefold() in {"age","age_years"}:labels.update({"age","aged","ages"})
    for label in sorted(labels,key=len,reverse=True):
        text=re.sub(r"(?<!\w)"+re.escape(label)+r"(?!\w)"," ",text,flags=re.I)
    units={_normalized_symbols(unit)}
    if unit=="%":units.add("percent")
    if unit in {"years","year"}:units.update({"year","years"})
    for value in sorted(units,key=len,reverse=True):
        text=re.sub(r"\s*".join(re.escape(c) for c in value)," ",text)
    text=re.sub(r"^\s*\d+[.)]\s*","",text)
    text=re.sub(r"\b(?:a|an|the|of|is|be|must|have|having|has|participants?|patients?|subjects?|at|screening|baseline|visit|between|from)\b"," ",text)
    return re.sub(r"[\s*.,;:()\[\]]+","",text)


def _shared_range(fragment,atoms):
    """Two explicit bounds sharing one quoted measurement, including intervals."""
    if len(atoms)<2 or any(a.criterion.numeric_constraint is None for a in atoms):
        raise UnsupportedSourceLogic("overlapping source quotes cannot be assigned to distinct predicates")
    constraints=[a.criterion.numeric_constraint for a in atoms]
    if len({(c.concept,c.unit) for c in constraints})!=1:
        raise UnsupportedSourceLogic("shared bound has multiple concepts; source scope is not verified")
    text=_normalized_symbols(fragment)
    matches=list(re.finditer(r"(>=|<=|>|<)\s*(\d+(?:\.\d+)?)",text))
    op_map={">=":"gte","<=":"lte",">":"gt","<":"lt"}
    requested={(c.operator.value,c.threshold) for c in constraints}
    concept,unit=constraints[0].concept,constraints[0].unit
    if all(c.operator.value=="eq" for c in constraints) and "ecog" in concept.casefold():
        values=re.search(r"(?<!\d)([0-5](?:\s*,\s*[0-5])*)\s+or\s+([0-5])(?!\d)",text)
        if values and {c.threshold for c in constraints}=={float(v) for v in re.findall(r"\d",values[0])}:
            if not _range_padding(text[:values.start()],concept,unit) and not _range_padding(text[values.end():],concept,unit):
                return _node("any",[_leaf(a.criterion.criterion_id) for a in atoms])
        raise UnsupportedSourceLogic("shared source is not a fully represented categorical value set")
    if len(atoms)!=2:
        raise UnsupportedSourceLogic("a numeric interval needs exactly two represented bounds")
    if len(matches)==2 and {(op_map[m[1]],float(m[2])) for m in matches}==requested:
        middle=_range_padding(text[matches[0].end():matches[1].start()],concept,unit)
        if middle not in {"and","to"}:
            raise UnsupportedSourceLogic("range connector or intermediate context is unsupported")
        if _range_padding(text[:matches[0].start()],concept,unit) or _range_padding(text[matches[1].end():],concept,unit):
            raise UnsupportedSourceLogic("range has unrepresented context or qualifiers")
    else:
        interval=re.search(r"(?:\bbetween\s+(\d+(?:\.\d+)?)\s+and\s+(\d+(?:\.\d+)?)|(?:\bfrom\s+)?(\d+(?:\.\d+)?)\s*(?:-|–|\bto\b)\s*(\d+(?:\.\d+)?))",text)
        bounds=[float(v) for v in interval.groups() if v is not None] if interval else []
        if not interval or requested!={("gte",bounds[0]),("lte",bounds[1])}:
            raise UnsupportedSourceLogic("shared quote is not a supported closed interval")
        if _range_padding(text[:interval.start()],concept,unit) or _range_padding(text[interval.end():],concept,unit):
            raise UnsupportedSourceLogic("interval has unrepresented context or qualifiers")
    return _node("all",[_leaf(a.criterion.criterion_id) for a in atoms])


_NUMBER_WORDS={"one":1,"two":2,"three":3,"four":4,"five":5}
_QUANTIFIER=re.compile(r"^\s*(?:at\s+least\s+)?(one|two|three|four|five|any|either|all|both|\d+)\s+(?:or\s+more\s+)?of\s+the\s+following\b[^:]*:\s*",re.I)
_TOKEN=re.compile(r"§\d+§|\b(?:and|or|with)\b|[()]")


def _parse_tokens(tokens,ids):
    position=0
    def level():
        nonlocal position
        terms=[]
        operators=[]
        expecting=True
        while position<len(tokens) and tokens[position]!=")":
            token=tokens[position]
            if token in {"and","or","with"}:
                if expecting:raise UnsupportedSourceLogic("source has an incomplete connector")
                operators.append(token)
                position+=1
                expecting=True
                continue
            if not expecting:operators.append("and")
            if token=="(":
                position+=1
                term=level()
                if position>=len(tokens) or tokens[position]!=")":
                    raise UnsupportedSourceLogic("unbalanced source parentheses")
                position+=1
            elif token.startswith("§"):
                term=_leaf(ids[int(token[1:-1])])
                position+=1
            else:raise UnsupportedSourceLogic("unknown source token")
            terms.append(term)
            expecting=False
        if expecting or not terms:raise UnsupportedSourceLogic("empty source relation")
        # English 'and/or' mixtures without explicit scope are not assumed to
        # have programming-language precedence. 'with' binds its local clause.
        if "and" in operators and "or" in operators:
            raise UnsupportedSourceLogic("mixed AND/OR requires explicit source scope")
        chunks=[[terms[0]]]
        for op,term in zip(operators,terms[1:]):
            if op=="or":chunks.append([term])
            else:chunks[-1].append(term)
        nodes=[chunk[0] if len(chunk)==1 else _node("all",chunk) for chunk in chunks]
        return nodes[0] if len(nodes)==1 else _node("any",nodes)
    result=level()
    if position!=len(tokens):raise UnsupportedSourceLogic("extra source parenthesis")
    return result


def parse_source_membership(text: str,start: int,end: int,atoms: list[GroundedLogicAtom]) -> CriterionLogic:
    if not atoms or len({a.criterion.kind for a in atoms})!=1:
        raise UnsupportedSourceLogic("a source group must contain one criterion kind")
    if any(a.start<start or a.end>end for a in atoms):
        raise UnsupportedSourceLogic("group source does not contain every referenced atom")
    fragment=text[start:end]
    if "§" in fragment:raise UnsupportedSourceLogic("reserved source marker")
    ordered=sorted(atoms,key=lambda a:(a.start,a.end,a.criterion.criterion_id))
    if any(a.end>b.start for a,b in zip(ordered,ordered[1:])):
        return _shared_range(fragment,ordered)
    # A numeric shortcut cannot absorb another clinical alternative inside its
    # own quote. A whole opaque prose atom can still be judged as prose.
    for atom in ordered:
        snippet=text[atom.start:atom.end]
        if atom.criterion.numeric_constraint is None and len(ordered)==1 and _QUANTIFIER.match(snippet):
            raise UnsupportedSourceLogic("a whole opaque clause does not prove the internal proposed group")
        if atom.criterion.numeric_constraint is not None and re.search(r"\b(?:or|if|unless|except)\b",text[atom.start:atom.end],re.I):
            if not re.search(r"\bor\s+(?:older|younger|over|more|less|under)\b",text[atom.start:atom.end],re.I):
                raise UnsupportedSourceLogic("numeric atom contains an unrepresented alternative or qualifier")
    masked=fragment
    for i,atom in reversed(list(enumerate(ordered))):
        masked=masked[:atom.start-start]+f"§{i}§"+masked[atom.end-start:]
    masked=re.sub(r"(?im)^\s*(?:inclusion|exclusion)\s+criteria\s*:\s*","",masked)
    masked=re.sub(r"(?m)^\s*(?:[-*+]|\d+[.)])\s*","",masked).strip()
    quantifier=_QUANTIFIER.match(masked)
    minimum=None
    if quantifier:
        raw=quantifier[1].casefold()
        minimum=len(ordered) if raw in {"all","both"} else (1 if raw in {"any","either"} else _NUMBER_WORDS.get(raw,int(raw) if raw.isdigit() else None))
        if raw=="both" and len(ordered)!=2:
            raise UnsupportedSourceLogic("both requires exactly two source items")
        masked=masked[quantifier.end():]
    masked=re.sub(r"^\s*(?:either|both)\s+","",masked,flags=re.I)
    matches=list(_TOKEN.finditer(masked))
    remainder=_TOKEN.sub("",masked)
    remainder=re.sub(r"\b(?:must\s+(?:apply|be\s+met)|is\s+required)\b","",remainder,flags=re.I)
    if re.sub(r"[\s.,;:\-\[\]*]+","",remainder):
        raise UnsupportedSourceLogic("unrepresented words outside the cited atoms")
    tokens=[m[0].casefold() for m in matches]
    ids=[a.criterion.criterion_id for a in ordered]
    if minimum is not None:
        if any(t in {"with","(",")"} for t in tokens) or not 1<=minimum<=len(ids):
            raise UnsupportedSourceLogic("quantified source list has unsupported nested scope")
        return _node("at_least",[_leaf(cid) for cid in ids],minimum)
    return _parse_tokens(tokens,ids)


_SCOPE_MARKER=re.compile(r"\b(?:and(?!\s+higher\b)|both|or(?!\s+(?:older|younger|over|under|more|less)\b)|if|unless|except(?:ion)?|between|whichever)\b",re.I)


def _safe_metadata_conjunction(atom,snippet):
    requirement=atom.criterion.evidence_requirement
    if requirement is None or requirement.max_age_days is None:
        return False
    # Only a duration tail already checked by the evidence-requirement validator
    # is allowed inside a numeric shortcut. Other clinical conjunctions need atoms.
    tails=re.split(r"\band\b",snippet,flags=re.I)[1:]
    allowed=re.compile(r"\b(?:must|be|was|is|measured|obtained|collected|tested|recorded|dated|documented|within|the|past|last|days?|weeks?|hours?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b",re.I)
    return bool(tails) and all(not re.sub(r"[\s\d.,;:\-]+","",allowed.sub("",tail)) for tail in tails)


def compile_protocol_logic(text: str,atoms: list[GroundedLogicAtom],proposals: list[GroundedLogicProposal]) -> CompiledSourceLogic:
    consumed=set()
    spans=[]
    groups=[]
    issues=[]
    records=[]
    def accept(start,end,selected,proposal=None):
        refs={a.criterion.criterion_id for a in selected}
        enclosed={a.criterion.criterion_id for a in atoms if start<=a.start and a.end<=end}
        if refs!=enclosed or refs & consumed:
            raise UnsupportedSourceLogic("group omits or reuses an enclosed criterion")
        source_tree=parse_source_membership(text,start,end,selected)
        eligible=eligibility_from_membership(source_tree,selected[0].criterion.kind)
        groups.append(eligible)
        spans.append((start,end))
        consumed.update(refs)
        records.append({"source_start":start,"source_end":end,"criterion_ids":sorted(refs),
                        "source_membership":source_tree.model_dump(mode="json"),
                        "eligibility_logic":eligible.model_dump(mode="json"),
                        "model_relation_corrected":proposal is not None and _canonical(proposal)!=_canonical(source_tree)})
    by_id={a.criterion.criterion_id:a for a in atoms}
    for proposal in proposals:
        try:
            selected=[by_id[cid] for cid in proposal.predicate.referenced_criterion_ids()]
            accept(proposal.start,proposal.end,selected,proposal.predicate)
        except (UnsupportedSourceLogic,KeyError) as error:
            issues.append(f"모델이 제안한 원문 논리 묶음을 검증하지 못했다: {error}")
    offset=0
    for line in text.splitlines(keepends=True):
        start,end=offset,offset+len(line.rstrip("\r\n"))
        offset+=len(line)
        selected=[a for a in atoms if a.criterion.criterion_id not in consumed and start<=a.start and a.end<=end]
        if len(selected)<2:continue
        try:accept(start,end,selected)
        except UnsupportedSourceLogic as error:
            issues.append(f"분리된 조건의 원문 연결 관계를 검증하지 못했다: {error}")
    for match in _SCOPE_MARKER.finditer(text):
        if any(start<=match.start() and match.end()<=end for start,end in spans):continue
        covering=[a for a in atoms if a.start<=match.start() and match.end()<=a.end]
        if len(covering)==1 and covering[0].criterion.numeric_constraint is None:
            continue  # Preserve a whole prose condition; do not flatten its internals.
        if (len(covering)==1 and match.group().casefold()=="and" and
            _safe_metadata_conjunction(covering[0],text[covering[0].start:covering[0].end])):
            continue
        issues.append("원문에 범위·대안·예외가 있지만 검증된 논리 묶음으로 보존되지 않았다.")
    for atom in atoms:
        if atom.criterion.criterion_id in consumed or atom.criterion.numeric_constraint is None:continue
        if re.search(r"\d+(?:\.\d+)?\s*(?:-|–|\bto\b)\s*\d+(?:\.\d+)?",text[atom.start:atom.end]):
            issues.append("원문의 양쪽 범위를 하나의 수치 조건만으로 계산할 수 없다.")
    groups.extend(_leaf(a.criterion.criterion_id) for a in atoms if a.criterion.criterion_id not in consumed)
    root=_node("all",groups) if records else None
    return CompiledSourceLogic(root,tuple(spans),tuple(dict.fromkeys(issues)),tuple(records))
