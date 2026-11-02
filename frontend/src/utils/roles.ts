import type { RoleOption } from '@/constants/assetRoles'
import type { RoleSpec } from '@/types/case-info'

export interface RoleContext {
  kind?: 'video' | 'image'
  mode: string
  /** 不能由用户选的角色：装配器自动附带的基图/控制图在这里排除。 */
  exclude?: readonly string[]
}

/**
 * 图片流程（patchimg / fullRedrawImg）过滤角色词表用的上下文。
 *
 * 图片提交没有视频的生成模式，词表里的图片参考角色（lighting_ref 等）挂在
 * `allMediaRef` 这一栏下，所以用它的名字做过滤上下文；这个值只用来挑下拉
 * 选项，不会进 `params`、也不会提交给平台。
 */
export const IMAGE_ROLE_MODE = 'allMediaRef'

/**
 * 图片流程里由装配器自动附带的角色，用户不能挂到自己的参考图上。
 *
 * 基图固定是 `edit_master`、mask 固定由 extract 产出（mask 本身是 reserved，
 * 已经选不出来）。视频流程的 `edit_master` 仍是用户可选的母版，所以这条只在
 * 图片流程生效。
 */
export const IMAGE_AUTO_ROLES = ['edit_master'] as const

/**
 * 提交清单里一行素材的可选角色。
 *
 * 词表只来自服务端（`/api/case` 的 `roles`）：这里只做三道过滤——`open`
 * 决定是否可新选，`kinds` 决定素材种类，`modes` 决定当前生成模式。当前行
 * 已有的角色若不在过滤结果里（旧 case），补一条「旧角色」，保证下拉里能显示
 * 原值而不是空。
 *
 * 图片流程（patchimg / fullRedrawImg）没有视频的生成模式，调用方传
 * `IMAGE_ROLE_MODE`：它只是角色词表的过滤上下文，不会提交给平台。
 */
export function optionsFor(
  roles: RoleSpec[],
  ctx: RoleContext,
  current?: string,
): RoleOption[] {
  const options: RoleOption[] = roles
    .filter((role) => role.open && !role.reserved)
    .filter((role) => !ctx.kind || !role.kinds || role.kinds.includes(ctx.kind))
    .filter((role) => role.modes.includes(ctx.mode))
    .filter((role) => !ctx.exclude?.includes(role.name))
    .map((role) => ({ value: role.name, label: role.label, help: role.help }))

  if (current && !options.some((option) => option.value === current)) {
    const spec = roles.find((role) => role.name === current)
    if (spec) {
      options.push({
        value: spec.name,
        label: `${spec.label}（旧角色）`,
        help: spec.help,
        legacy: true,
      })
    }
  }
  return options
}
