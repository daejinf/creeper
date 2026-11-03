import type { SessionMemberSeed } from "./types";

export const demoMembers: SessionMemberSeed[] = [
  { member_id: "u-8f1c", nickname: "지민", avatar_color: "violet" },
  { member_id: "u-2a7e", nickname: "서준", avatar_color: "mint" },
  { member_id: "u-6d4b", nickname: "하린", avatar_color: "amber" }
];

export const suggestionByDimension: Record<string, string[]> = {
  goal: ["자격증 합격을 목표로 해요.", "프로젝트 완성을 목표로 해요.", "꾸준한 습관을 만드는 게 목표예요."],
  metric_target: ["각자 문제 20개를 완료해요.", "그룹 합계 60개를 완료해요.", "주간 달성률 80%를 목표로 해요."],
  timeline: ["2026-11-02부터 2026-11-29까지 진행해요.", "2026-11-09부터 2026-12-06까지 진행해요.", "날짜는 조금 더 논의할게요."],
  frequency: ["주 2회 진행해요.", "주 3회 진행해요.", "주 5회 진행해요."],
  verification: ["코드 화면으로 인증해요.", "사진과 짧은 설명으로 인증해요.", "체크리스트로 인증해요."],
  checkin: ["금요일 20시에 체크인해요.", "일요일 10시에 체크인해요.", "격주 일요일 20시에 체크인해요."],
  privacy: ["완료 여부만 공개해요.", "완료율까지 공개해요.", "활동 기록도 공개해요."],
  consensus: ["모두 동의했어요", "아직 더 논의할게요"]
};
