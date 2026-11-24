/** Pure helpers of the 立院QA preview: numbering as in the .docx, issue placement and the revision diff. */
import type { QaDocQuestion, QaDocSection, QaDocument, QaIssue } from "../api/qa";

const NUMERALS = "零一二三四五六七八九十";

/** 1 -> 一, 11 -> 十一, 20 -> 二十; the same rule as the document renderer. */
export function chineseNumber(n: number): string {
    if (n <= 10) return NUMERALS[n];
    if (n < 20) return `十${NUMERALS[n - 10]}`;
    const ones = n % 10;
    return `${NUMERALS[Math.floor(n / 10)]}十${ones ? NUMERALS[ones] : ""}`;
}

/** 一、heading： for a section; a heading-less section numbers its paragraphs 一、二、… instead of (一)(二). */
export const sectionTitle = (section: QaDocSection, index: number): string | null =>
    section.heading === null ? null : `${chineseNumber(index + 1)}、${section.heading}：`;
export const paragraphLabel = (section: QaDocSection, index: number): string =>
    section.heading === null ? `${chineseNumber(index + 1)}、` : `(${chineseNumber(index + 1)})`;

export type PreviewPart = "short" | "detail";

export const partSections = (question: QaDocQuestion, part: PreviewPart): QaDocSection[] => question[part];

/** Unresolved issues of one question and part (every kind of location). */
export const issuesOf = (document: QaDocument, no: number, part?: PreviewPart): QaIssue[] =>
    document.issues.filter((issue) => issue.question_no === no && (part === undefined || issue.part === part));

export interface PartIssues {
    /** Not tied to a section: shown at the top of the part. */
    top: QaIssue[];
    /** Tied to a section but not a paragraph: shown beside the heading. */
    bySection: Map<number, QaIssue[]>;
    byParagraph: Map<string, QaIssue[]>;
}

export const paragraphKey = (section: number, paragraph: number) => `${section}:${paragraph}`;

/** Places a part's issues; one that points past the content falls back to the nearest level that exists. */
export function placeIssues(issues: QaIssue[], sections: QaDocSection[]): PartIssues {
    const placed: PartIssues = { top: [], bySection: new Map(), byParagraph: new Map() };
    const add = (map: Map<number | string, QaIssue[]>, key: number | string, issue: QaIssue) =>
        map.set(key, [...(map.get(key) ?? []), issue]);
    for (const issue of issues) {
        const section = issue.section ?? null;
        const paragraph = issue.paragraph ?? null;
        const target = section === null ? undefined : sections[section];
        if (!target) placed.top.push(issue);
        else if (paragraph === null || paragraph >= target.paragraphs.length) add(placed.bySection, section!, issue);
        else add(placed.byParagraph, paragraphKey(section!, paragraph), issue);
    }
    return placed;
}

/** Paragraph keys (`question:part:section:paragraph`) whose text differs from the base version's. */
export function changedParagraphs(base: QaDocument, current: QaDocument): Set<string> {
    const changed = new Set<string>();
    for (const question of current.questions) {
        const before = base.questions.find((item) => item.no === question.no);
        for (const part of ["short", "detail"] as const) {
            question[part].forEach((section, sectionIndex) => {
                section.paragraphs.forEach((paragraph, index) => {
                    const old = before?.[part][sectionIndex]?.paragraphs[index];
                    if (!old || old.text !== paragraph.text) {
                        changed.add(changedKey(question.no, part, sectionIndex, index));
                    }
                });
            });
        }
    }
    return changed;
}

export const changedKey = (no: number, part: PreviewPart, section: number, paragraph: number) =>
    `${no}:${part}:${section}:${paragraph}`;
