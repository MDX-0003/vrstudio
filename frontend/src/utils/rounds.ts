import type { ImageRound, RoundSummary } from '@/types/case-info'

/** 预演轮次不是成片，默认不进入生成记录列表。 */
export function visibleRounds(rounds: RoundSummary[]): RoundSummary[] {
  return rounds.filter((round) => round.stage !== 'prepared')
}

export function roundResultType(round: RoundSummary): 'success' | 'danger' | 'info' | 'warning' {
  if (round.result === 'yes') return 'success'
  if (round.stage === 'failed') return 'danger'
  if (round.stage === 'prepared') return 'warning'
  return 'info'
}

export function roundResultText(round: RoundSummary): string {
  if (round.result === 'yes') return '可用'
  if (round.stage === 'failed') return '生成失败'
  if (round.stage === 'prepared') return '仅预演'
  return '未出片'
}

/** 图片候选网格默认展示最新一轮；后端已按 mtime 倒序给出。 */
export function latestImageRound(rounds: ImageRound[] | undefined): ImageRound | null {
  return rounds?.length ? rounds[0] : null
}

/**
 * 最新有结果的轮次。空 prepared 轮次（提交前先建好、随后没跑成）会排在
 * mtime 最前，但它在页面上是一条空候选；候选网格改用这个函数取数。
 */
export function latestResultRound(rounds: ImageRound[] | undefined): ImageRound | null {
  return rounds?.find((round) => round.results.length) ?? null
}

/** 已采用的图片轮次；采用的可能不是最新一轮，候选面板底部要单独标出来。 */
export function adoptedImageRound(rounds: ImageRound[] | undefined): ImageRound | null {
  return rounds?.find((round) => round.adopted) ?? null
}

/**
 * 图片流程的台账状态：刷新页面或重启 serve 后仍能如实回显。
 * 本地运行态（预演/提交中/运行中）优先，这个函数只负责「没有本地运行态」时的兜底。
 */
export function imageRoundStatus(rounds: ImageRound[] | undefined): string {
  const latest = latestImageRound(rounds)
  if (!latest) return '未预演'
  // 最新一次失败优先暴露；最新是空 prepared 轮时，只要有更早的结果仍算已完成。
  if (latest.stage === 'failed') return '失败'
  if (latestResultRound(rounds)) return '已完成'
  return '仅预演'
}
