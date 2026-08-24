/**
 * 文档版本 diff 工具（Epic 139 配套）
 *
 * 从 KnowledgeVault `revision-diff.ts` 移植的 Hirschberg LCS 实现：
 * - 行级 diff：基于 LCS 找最长公共子序列；
 * - 词级 diff：在同号行内再做一次 token-level diff 高亮变化处；
 * - 折叠 unchanged 段：保留每段上下文 3 行。
 *
 * 零依赖；不引 diff lib（FR-12 红线）。
 */

export type DiffFragmentKind = 'unchanged' | 'added' | 'removed';

export interface DiffFragment {
  text: string;
  kind: DiffFragmentKind;
}

export interface RevisionDiffRow {
  oldLineNumber: number | null;
  newLineNumber: number | null;
  oldFragments: DiffFragment[];
  newFragments: DiffFragment[];
  kind: 'unchanged' | 'changed' | 'removed' | 'added';
}

export interface RevisionDiffBlock {
  kind: 'rows' | 'collapsed';
  rows: RevisionDiffRow[];
  /** collapsed 块的大小（行数），用于 UI 提示 "展开 N 行"。 */
  hiddenCount?: number;
}

const CONTEXT_LINE_COUNT = 3;

export function buildRevisionDiff(oldContent: string, newContent: string): RevisionDiffBlock[] {
  const oldLines = splitLines(oldContent);
  const newLines = splitLines(newContent);
  const pairs = findLcsPairs(oldLines, newLines);
  const rows: RevisionDiffRow[] = [];
  let oldIndex = 0;
  let newIndex = 0;

  for (const [matchingOldIndex, matchingNewIndex] of pairs) {
    appendChangedRows(
      rows, oldLines.slice(oldIndex, matchingOldIndex), newLines.slice(newIndex, matchingNewIndex),
      oldIndex, newIndex,
    );
    rows.push(unchangedRow(oldLines[matchingOldIndex], matchingOldIndex + 1, matchingNewIndex + 1));
    oldIndex = matchingOldIndex + 1;
    newIndex = matchingNewIndex + 1;
  }

  appendChangedRows(rows, oldLines.slice(oldIndex), newLines.slice(newIndex), oldIndex, newIndex);
  return collapseUnchangedRows(rows);
}

function splitLines(content: string): string[] {
  return content === '' ? [] : content.replace(/\r\n/g, '\n').split('\n');
}

function appendChangedRows(
  rows: RevisionDiffRow[],
  oldLines: string[],
  newLines: string[],
  oldOffset: number,
  newOffset: number,
): void {
  const count = Math.max(oldLines.length, newLines.length);
  for (let index = 0; index < count; index += 1) {
    const oldLine = oldLines[index];
    const newLine = newLines[index];
    if (oldLine !== undefined && newLine !== undefined) {
      const [oldFragments, newFragments] = wordDiff(oldLine, newLine);
      rows.push({
        oldLineNumber: oldOffset + index + 1,
        newLineNumber: newOffset + index + 1,
        oldFragments, newFragments, kind: 'changed',
      });
    } else if (oldLine !== undefined) {
      rows.push({
        oldLineNumber: oldOffset + index + 1, newLineNumber: null,
        oldFragments: [{ text: oldLine, kind: 'removed' }], newFragments: [], kind: 'removed',
      });
    } else if (newLine !== undefined) {
      rows.push({
        oldLineNumber: null, newLineNumber: newOffset + index + 1,
        oldFragments: [], newFragments: [{ text: newLine, kind: 'added' }], kind: 'added',
      });
    }
  }
}

function unchangedRow(line: string, oldLineNumber: number, newLineNumber: number): RevisionDiffRow {
  return {
    oldLineNumber, newLineNumber,
    oldFragments: [{ text: line, kind: 'unchanged' }],
    newFragments: [{ text: line, kind: 'unchanged' }],
    kind: 'unchanged',
  };
}

function collapseUnchangedRows(rows: RevisionDiffRow[]): RevisionDiffBlock[] {
  const blocks: RevisionDiffBlock[] = [];
  let index = 0;
  while (index < rows.length) {
    if (rows[index].kind !== 'unchanged') {
      blocks.push({ kind: 'rows', rows: [rows[index]] });
      index += 1;
      continue;
    }
    const start = index;
    while (index < rows.length && rows[index].kind === 'unchanged') index += 1;
    const unchanged = rows.slice(start, index);
    if (unchanged.length <= CONTEXT_LINE_COUNT * 2) {
      blocks.push({ kind: 'rows', rows: unchanged });
      continue;
    }
    blocks.push({ kind: 'rows', rows: unchanged.slice(0, CONTEXT_LINE_COUNT) });
    blocks.push({ kind: 'collapsed', rows: [], hiddenCount: unchanged.length - CONTEXT_LINE_COUNT * 2 });
    blocks.push({ kind: 'rows', rows: unchanged.slice(-CONTEXT_LINE_COUNT) });
  }
  return blocks;
}

function wordDiff(oldLine: string, newLine: string): [DiffFragment[], DiffFragment[]] {
  const oldTokens = tokenize(oldLine);
  const newTokens = tokenize(newLine);
  const pairs = findLcsPairs(oldTokens, newTokens);
  const oldFragments: DiffFragment[] = [];
  const newFragments: DiffFragment[] = [];
  let oldIndex = 0;
  let newIndex = 0;

  for (const [matchingOldIndex, matchingNewIndex] of pairs) {
    pushFragment(oldFragments, oldTokens.slice(oldIndex, matchingOldIndex).join(''), 'removed');
    pushFragment(newFragments, newTokens.slice(newIndex, matchingNewIndex).join(''), 'added');
    pushFragment(oldFragments, oldTokens[matchingOldIndex], 'unchanged');
    pushFragment(newFragments, newTokens[matchingNewIndex], 'unchanged');
    oldIndex = matchingOldIndex + 1;
    newIndex = matchingNewIndex + 1;
  }
  pushFragment(oldFragments, oldTokens.slice(oldIndex).join(''), 'removed');
  pushFragment(newFragments, newTokens.slice(newIndex).join(''), 'added');
  return [oldFragments, newFragments];
}

function tokenize(line: string): string[] {
  return line.match(/\s+|[\p{L}\p{N}_]+|[^\s\p{L}\p{N}_]/gu) ?? [];
}

function pushFragment(fragments: DiffFragment[], text: string, kind: DiffFragmentKind): void {
  if (text) fragments.push({ text, kind });
}

function findLcsPairs<T>(oldValues: T[], newValues: T[]): Array<[number, number]> {
  const pairs: Array<[number, number]> = [];
  collectLcsPairs(oldValues, 0, oldValues.length, newValues, 0, newValues.length, pairs);
  return pairs;
}

/** Hirschberg 算法：O(mn) 时间、O(m+n) 空间，适合长 Markdown 文档。 */
function collectLcsPairs<T>(
  oldValues: T[], oldStart: number, oldEnd: number,
  newValues: T[], newStart: number, newEnd: number,
  pairs: Array<[number, number]>,
): void {
  if (oldStart === oldEnd || newStart === newEnd) return;
  if (oldEnd - oldStart === 1) {
    const match = newValues.indexOf(oldValues[oldStart], newStart);
    if (match >= 0 && match < newEnd) pairs.push([oldStart, match]);
    return;
  }
  const oldMiddle = Math.floor((oldStart + oldEnd) / 2);
  const forward = lcsLengths(oldValues, oldStart, oldMiddle, newValues, newStart, newEnd, false);
  const backward = lcsLengths(oldValues, oldMiddle, oldEnd, newValues, newStart, newEnd, true);
  let split = 0;
  let bestScore = -1;
  for (let index = 0; index <= newEnd - newStart; index += 1) {
    const score = forward[index] + backward[newEnd - newStart - index];
    if (score > bestScore) { bestScore = score; split = index; }
  }
  const newMiddle = newStart + split;
  collectLcsPairs(oldValues, oldStart, oldMiddle, newValues, newStart, newMiddle, pairs);
  collectLcsPairs(oldValues, oldMiddle, oldEnd, newValues, newMiddle, newEnd, pairs);
}

function lcsLengths<T>(
  oldValues: T[], oldStart: number, oldEnd: number,
  newValues: T[], newStart: number, newEnd: number,
  reverse: boolean,
): number[] {
  const length = newEnd - newStart;
  let previous = new Array<number>(length + 1).fill(0);
  for (let offset = 0; offset < oldEnd - oldStart; offset += 1) {
    const oldValue = oldValues[reverse ? oldEnd - offset - 1 : oldStart + offset];
    const current = new Array<number>(length + 1).fill(0);
    for (let newOffset = 0; newOffset < length; newOffset += 1) {
      const newValue = newValues[reverse ? newEnd - newOffset - 1 : newStart + newOffset];
      current[newOffset + 1] = oldValue === newValue
        ? previous[newOffset] + 1
        : Math.max(previous[newOffset + 1], current[newOffset]);
    }
    previous = current;
  }
  return previous;
}
