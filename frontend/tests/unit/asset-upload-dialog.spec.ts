import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/uploads', () => ({
  createUpload: vi.fn(async () => ({
    upload_id: 'u1',
    chunk_size: 8 * 1024 * 1024,
    max_size: 2 * 1024 * 1024 * 1024,
  })),
  getUpload: vi.fn(),
  putChunk: vi.fn(() => new Promise(() => undefined)),
  completeUpload: vi.fn(),
  cancelUpload: vi.fn(async () => ({ deleted: 'u1' })),
  cancelUploadKeepalive: vi.fn(),
}))
vi.mock('@/api/case', () => ({
  addAssetsByUploads: vi.fn(),
}))

import AssetUploadDialog from '@/components/upload/AssetUploadDialog.vue'
import { cancelUpload, cancelUploadKeepalive, createUpload, putChunk } from '@/api/uploads'

const DialogStub = defineComponent({
  props: { modelValue: Boolean, title: String, width: String },
  template: '<div><slot /><slot name="footer" /></div>',
})

const ButtonStub = defineComponent({
  props: { disabled: Boolean, loading: Boolean },
  template: '<button :disabled="disabled"><slot /></button>',
})

const stubs = {
  'el-dialog': DialogStub,
  'el-button': ButtonStub,
  'el-icon': true,
  'el-select': true,
  'el-option': true,
  'el-alert': true,
  'el-empty': true,
  'el-progress': true,
}

describe('AssetUploadDialog', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('cancels the server upload when the dialog unmounts', async () => {
    const wrapper = mount(AssetUploadDialog, {
      props: { modelValue: true, caseName: 'p1' },
      global: { stubs },
    })

    const input = wrapper.find('input[type="file"]')
    const file = new File(['hello'], 'ref.png', { type: 'image/png' })
    Object.defineProperty(input.element, 'files', { value: [file] })
    await input.trigger('change')
    await flushPromises()

    expect(createUpload).toHaveBeenCalledWith('ref.png', 5)
    expect(putChunk).toHaveBeenCalled()

    wrapper.unmount()
    await flushPromises()

    expect(cancelUpload).toHaveBeenCalledWith('u1')
  })

  it('uses keepalive cancel on pagehide and does not duplicate on unmount', async () => {
    const wrapper = mount(AssetUploadDialog, {
      props: { modelValue: true, caseName: 'p1' },
      global: { stubs },
    })

    const input = wrapper.find('input[type="file"]')
    const file = new File(['hello'], 'ref.png', { type: 'image/png' })
    Object.defineProperty(input.element, 'files', { value: [file] })
    await input.trigger('change')
    await flushPromises()

    window.dispatchEvent(new Event('pagehide'))
    expect(cancelUploadKeepalive).toHaveBeenCalledWith('u1')
    expect(cancelUpload).not.toHaveBeenCalled()

    wrapper.unmount()
    await flushPromises()
    expect(cancelUpload).not.toHaveBeenCalled()
  })
})
