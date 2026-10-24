export type SaveState = 'saved' | 'saving' | 'unsaved'

/** 页头的保存状态要覆盖提示词、参数与素材，而不是只看提示词。 */
export function aggregateSaveState(prompt: SaveState, busy: boolean): SaveState {
  return busy ? 'saving' : prompt
}
