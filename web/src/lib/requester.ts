import { useEffect, useState } from "react";

// 로그인이 없으므로 요청자 이름은 이 브라우저에만 기억한다(작업 목록 표시용).
const KEY = "cc_requester";

export function loadRequester(): string {
  try {
    return localStorage.getItem(KEY) ?? "";
  } catch {
    return ""; // 사생활 보호 모드 등 저장소를 못 쓰면 빈 이름으로 동작한다
  }
}

export function useRequester(): [string, (value: string) => void] {
  const [name, setName] = useState(loadRequester);
  useEffect(() => {
    try {
      localStorage.setItem(KEY, name);
    } catch {
      // 저장하지 못해도 화면은 계속 동작한다
    }
  }, [name]);
  return [name, setName];
}
