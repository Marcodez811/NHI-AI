from __future__ import annotations

from app.services.agentic.contracts import ReviewOutcome
from app.services.legislative_qa import review as qa_review
from app.services.legislative_qa.numeric import evidence_values, numeric_issues, unsupported_numbers
from app.services.legislative_qa.writer import AiOutput


def _missing(text: str, *quotes: str, context: tuple[str, ...] = ()) -> list[str]:
    from app.services.legislative_qa.numeric import context_calendar_values

    return unsupported_numbers(text, evidence_values(list(quotes)) | context_calendar_values(list(context)))


def test_figures_found_in_the_quote_pass_and_others_are_listed_once():
    quote = "113年第4季一般服務點值為 0.9476，較上季成長 3.25%，共 1,234 家院所。"
    assert _missing("113年第4季點值為 0.9476，共1234家，成長3.25%。", quote) == []
    assert _missing("點值 0.9476，另有 0.9999 與 0.9999、57家。", quote) == ["0.9999", "57"]


def test_full_width_digits_and_thousand_separators_are_equal():
    assert _missing("共 1234 家，點值 0.94760", "共１,２３４家，點值０．９４７６") == []


def test_amount_written_forms_agree_by_value():
    assert _missing("預算 10.662億元", "全年預算為 10.662億元") == []
    assert _missing("預算 10.662億元", "預算 106,620 萬元") == []
    assert _missing("預算 1,066,200,000 元", "預算約 10.662億") == []
    assert _missing("預算 10億6,620萬元", "預算約 10.662億") == []
    assert _missing("預算 1,066,200 千元", "預算約 10.662億") == []
    # same digits, different unit: not the same amount
    assert _missing("預算 10.662萬元", "預算約 10.662億") == ["10.662萬"]
    assert _missing("成長 5", "成長 5 萬") == ["5"]


def test_western_years_in_the_evidence_match_roc_years():
    assert _missing("113年共服務 5 人", "2024年共服務 5 人") == []
    assert _missing("112年共服務 5 人", "2024年共服務 5 人") == ["112"]


def test_dates_and_quarters_must_be_in_the_evidence_unless_the_question_states_them():
    quote = "114年第1季點值 0.95"
    assert _missing("114年第1季點值 0.95", quote) == []
    assert _missing("114年第2季點值 0.95", quote) == ["2"]
    assert _missing("114年第2季點值 0.95", quote, context=("請問114年第2季的點值？",)) == []
    # a stated year does not whitelist an unrelated amount of the same value
    assert _missing("補助 114 億元", quote, context=("114年第2季",)) == ["114億"]


def test_numbering_markers_question_numbers_and_names_are_not_numbers():
    quote = "點值 0.95"
    text = "1. 點值 0.95\n2. 同上\n（3）補充\n(4) 補充"
    assert _missing(text, quote) == []
    assert _missing("如第 12 題所述，點值 0.95", quote) == []
    assert _missing("COVID-19 期間點值 0.95，B型肝炎 H1N1", quote) == []
    assert _missing("2.5 倍", quote) == ["2.5"]
    assert _missing("1.2 倍", "成長 1.2 倍") == []


def test_a_number_with_a_unit_needs_the_same_value_with_that_unit():
    quote = "112年起，全國121個鄉鎮設有院所，共 179 家，預算 5,000 萬元，成長 3%。"
    assert _missing("179家院所至121個鄉鎮", quote) == []
    assert _missing("179家院所至112個鄉鎮", quote) == ["112"]  # 112 only appears as 112年
    assert _missing("112年起", quote) == []
    assert _missing("共 179 所，預算 5,000 萬", quote) == []
    assert _missing("共 179 人", quote) == ["179"]
    assert _missing("成長 3 家", quote) == ["3"]
    assert _missing("成長 3％", quote) == []
    assert _missing("點值 0.9594 元", "點值 0.9594") == []  # a bare figure in the quote may carry a unit
    assert _missing("112年", "點值 112") == ["112"]  # but not a date's
    assert _missing("共 179", quote) == []  # no unit: the value alone decides


def test_range_and_list_units_apply_to_every_number_in_either_text():
    assert _missing("補助20萬元至41萬元", "補助金額為20至41萬元") == []
    assert _missing("補助20至41萬元", "補助金額為20萬元至41萬元") == []
    assert _missing("補助20～41萬元", "補助20萬元~41萬元") == []
    assert _missing("114年第2季", "114年第2季") == []
    assert _missing("第2季", "114年第2季") == []
    assert _missing("2季", "114年第2季") == []
    assert _missing("113年第1至第3季", "113年第1季、第2季至第3季") == []
    assert _missing("113年第1季至第3季", "113年第1至第3季") == []
    assert _missing("設置3、5、7家", "設置3家、5家、7家") == []
    assert _missing("設置3家、5家、7家", "設置3、5、7家") == []
    assert _missing("補助20至42萬元", "補助20萬元至41萬元") == ["42萬"]


def _frozen() -> dict:
    return {"questions": [{
        "no": 1, "text": "請說明 113年 點值", "short_points": ["114年第1季點值"],
        "detail": [{"title": "背景說明", "points": ["112年起"]}],
        "evidence": [
            {"id": "q1-e1", "quote": "113年點值 0.9476，金額 10.662億元"},
            {"id": "q1-e2", "quote": "共 57 家院所"},
        ],
    }]}


def _output(short_text: str, short_cites: list[str], detail_text: str = "沒有數字", detail_cites=()) -> AiOutput:
    return AiOutput.model_validate({"questions": [{
        "no": 1,
        "short": [{"heading": "成效", "paragraphs": [{"text": short_text, "cites": short_cites}]}],
        "detail": [{"heading": "背景說明", "paragraphs": [{"text": detail_text, "cites": list(detail_cites)}]}],
    }]})


def test_issues_are_per_paragraph_with_question_part_kind_and_place():
    output = _output("113年點值 0.9476。金額 10.662億元。", ["q1-e1"], "113年共 58 家，點值 0.9476。", ["q1-e1", "q1-e2"])
    issues = numeric_issues(_frozen(), output)
    assert [(i.question_no, i.part, i.kind) for i in issues] == [(1, "detail", "numeric")]
    assert "詳答「背景說明」第 1 段" in issues[0].message and "58" in issues[0].message


def test_only_the_cited_items_count_and_outline_years_are_allowed():
    # 57 is in q1-e2, which the paragraph does not cite
    issues = numeric_issues(_frozen(), _output("共 57 家院所。", ["q1-e1"]))
    assert [(i.part, i.kind) for i in issues] == [("short", "numeric")] and "57" in issues[0].message
    # 114年第1季 comes from the confirmed outline, 112年 from the detail outline
    assert numeric_issues(_frozen(), _output("114年第1季起，點值 0.9476。", ["q1-e1"], "112年起，共 57 家。", ["q1-e2"])) == []


def test_checker_findings_become_issues_and_unknown_ones_are_dropped():
    review = ReviewOutcome.model_validate({"summary": "x", "findings": [
        {"category": "Unsupported", "issue_key": "q1:detail:unsupported:a", "locations": ["q1:detail"], "description": "沒有依據", "correction": "刪除"},
        {"category": "unsupported", "issue_key": "q1:detail:unsupported:a", "locations": ["q1:detail"], "description": "重複"},
        {"category": "style", "issue_key": "q1:short:style:v", "locations": [], "description": "用了我們"},
        {"category": "numeric", "issue_key": "q1:short:numeric:n", "locations": ["q1:short"], "description": "不可"},
        {"category": "style", "issue_key": "q7:short:style:v", "locations": ["q7:short"], "description": "沒有這題"},
        {"category": "style", "issue_key": "nowhere", "locations": ["somewhere"], "description": "沒有位置"},
    ]})
    found = qa_review.checker_issues(review, {1})
    assert [(i.question_no, i.part, i.kind, i.message) for i, _ in found] == [
        (1, "detail", "unsupported", "沒有依據（建議：刪除）"), (1, "short", "style", "用了我們"),
    ]
    outcome = qa_review.to_outcome("s", found)
    assert [item.finding_id for item in outcome.findings] == [key for _, key in found]
    assert qa_review.outcome_issues(outcome) == [issue for issue, _ in found]
    text = qa_review.revision_feedback([issue for issue, _ in found])
    assert text.startswith("[QA-REVIEW]") and "第 1 題詳答［資料中沒有依據］" in text and "第 1 題簡答［格式或語氣］" in text
