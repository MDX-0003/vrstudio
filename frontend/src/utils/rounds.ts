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
