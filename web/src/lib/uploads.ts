import type { Engine } from "../api/types";

// 서버(contentcompare/web/uploads.py)와 같은 규칙. 서버가 최종 판정하지만, 관계없는 파일까지
// 올리지 않도록 화면에서도 먼저 거른다.
export const SUPPORTED_EXTS = [".xlsx", ".xls", ".xlsm", ".docx", ".doc", ".pptx", ".ppt"];
export const REFERENCE_EXTS = [".xlsx", ".xls", ".xlsm"];

export interface PickedFile {
  file: File;
  path: string;
}

function extOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i).toLowerCase() : "";
}

function baseOf(path: string): string {
  return path.split("/").pop() ?? path;
}

/** 폴더 업로드면 `자료/하위/a.docx`, 파일 선택이면 이름. */
export function relativePathOf(file: File): string {
  const relative = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
  return relative ? relative : file.name;
}

export function isReferenceName(name: string): boolean {
  return REFERENCE_EXTS.includes(extOf(name)) && !baseOf(name).startsWith("~$");
}

export function addTargets(existing: PickedFile[], files: File[]): { items: PickedFile[]; skipped: string[] } {
  const items = [...existing];
  const seen = new Set(existing.map((p) => p.path));
  const skipped: string[] = [];
  for (const file of files) {
    const path = relativePathOf(file);
    if (baseOf(path).startsWith("~$") || !SUPPORTED_EXTS.includes(extOf(path))) {
      skipped.push(path);
      continue;
    }
    if (seen.has(path)) continue;
    seen.add(path);
    items.push({ file, path });
  }
  return { items, skipped };
}

/**
 * 폼을 만든다. 상대 경로는 `target_paths` 로 따로 보내고 파일 이름은 마지막 조각만 쓴다 —
 * 브라우저마다 multipart filename 에 한글·폴더 경로를 싣는 방식이 달라서다(계획 2 Review Focus #1).
 */
export function buildJobForm(engine: Engine, requester: string, reference: File, targets: PickedFile[]): FormData {
  const form = new FormData();
  form.append("engine", engine);
  form.append("requester", requester.trim());
  form.append("reference", reference, reference.name);
  form.append("reference_path", reference.name);
  for (const t of targets) {
    form.append("targets", t.file, baseOf(t.path));
    form.append("target_paths", t.path);
  }
  return form;
}
