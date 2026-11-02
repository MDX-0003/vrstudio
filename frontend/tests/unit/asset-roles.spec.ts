import { describe, expect, it } from 'vitest'
import { defaultRole } from '@/constants/assetRoles'
import { IMAGE_AUTO_ROLES, IMAGE_ROLE_MODE, optionsFor } from '@/utils/roles'
import type { RoleSpec } from '@/types/case-info'

const ROLES: RoleSpec[] = [
  {
    name: 'edit_master',
    label: '编辑母版',
    help: '编辑母版（时间轴、构图与动作基准）',
    kinds: null,
    modes: ['videoEdit', 'allMediaRef', 'videoExtend'],
    open: true,
    reserved: false,
  },
  {
    name: 'subject_ref',
    label: '主体参考',
    help: '主体参考（只定义角色外观）',
    kinds: null,
    modes: ['videoEdit', 'allMediaRef', 'videoExtend'],
    open: true,
    reserved: false,
  },
  {
    name: 'lighting_ref',
    label: '光照参考',
    help: '光照参考（只提供光源位置、色温、明暗对比与空气质感）',
    kinds: ['image'],
    modes: ['videoEdit', 'allMediaRef', 'videoExtend'],
    open: true,
    reserved: false,
  },
  {
    name: 'scene_ref',
    label: '场景参考',
    help: '场景参考（只提供构图、背景与光照）',
    kinds: null,
    modes: ['videoEdit', 'allMediaRef', 'videoExtend'],
    open: false,
    reserved: false,
  },
  {
    name: 'first_frame',
    label: '首帧定稿',
    help: '首帧定稿图（视频第一帧，锁起点构图与人物外观）',
    kinds: ['image'],
    modes: ['firstLastFrame'],
    open: true,
    reserved: false,
  },
  {
    name: 'last_frame',
    label: '尾帧定稿',
    help: '尾帧定稿图（视频最后一帧，锁终点位置、大小与朝向）',
    kinds: ['image'],
    modes: ['firstLastFrame'],
    open: true,
    reserved: false,
  },
  {
    name: 'mask',
    label: '编辑区域标注',
    help: '编辑区域标注（流程产物，装配器自动附带）',
    kinds: ['image'],
    modes: [],
    open: false,
    reserved: true,
  },
]

describe('submit role options', () => {
  it('offers lighting_ref for images only', () => {
    const imageNames = optionsFor(ROLES, { kind: 'image', mode: 'videoEdit' }).map(
      (option) => option.value,
    )
    const videoNames = optionsFor(ROLES, { kind: 'video', mode: 'videoEdit' }).map(
      (option) => option.value,
    )
    expect(imageNames).toContain('lighting_ref')
    expect(videoNames).not.toContain('lighting_ref')
    expect(videoNames).toEqual(['edit_master', 'subject_ref'])
  })

  it('hides closed roles, reserved roles and other modes', () => {
    const names = optionsFor(ROLES, { kind: 'image', mode: 'videoEdit' }).map(
      (option) => option.value,
    )
    expect(names).not.toContain('scene_ref')
    expect(names).not.toContain('mask')
    expect(names).not.toContain('first_frame')

    const firstLast = optionsFor(ROLES, { kind: 'image', mode: 'firstLastFrame' }).map(
      (option) => option.value,
    )
    expect(firstLast).toEqual(['first_frame', 'last_frame'])
  })

  it('offers only reference roles for image flows', () => {
    const names = optionsFor(ROLES, {
      kind: 'image',
      mode: IMAGE_ROLE_MODE,
      exclude: IMAGE_AUTO_ROLES,
    }).map((option) => option.value)
    expect(names).toContain('lighting_ref')
    expect(names).toContain('subject_ref')
    expect(names).not.toContain('edit_master')
    expect(names).not.toContain('first_frame')
  })

  it('keeps the current role visible as a legacy option', () => {
    const options = optionsFor(ROLES, { kind: 'image', mode: 'videoEdit' }, 'scene_ref')
    const legacy = options.find((option) => option.value === 'scene_ref')
    expect(legacy?.label).toBe('场景参考（旧角色）')
    expect(legacy?.legacy).toBe(true)
  })

  it('keeps subject_ref as the default image role', () => {
    expect(defaultRole('image')).toBe('subject_ref')
    expect(defaultRole('video')).toBe('motion_ref')
  })
})
