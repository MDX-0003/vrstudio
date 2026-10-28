import type { RoleOption } from '@/constants/assetRoles'
import type { RoleSpec } from '@/types/case-info'

export interface RoleContext {
  kind?: 'video' | 'image'
  mode: string
}

/**
 * 提交清单里一行素材的可选角色。
 *
 * 词表只来自服务端（`/api/case` 的 `roles`）：这里只做三道过滤——`open`
 * 决定是否可新选，`kinds` 决定素材种类，`modes` 决定当前生成模式。当前行
 * 已有的角色若不在过滤结果里（旧 case），补一条「旧角色」，保证下拉里能显示
 * 原值而不是空。
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
