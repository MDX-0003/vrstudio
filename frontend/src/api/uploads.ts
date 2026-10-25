import { deleteJson, getJson, postJson, putBlob } from './client'

export interface UploadSession {
  upload_id: string
  chunk_size: number
  max_size: number
}

export interface UploadStatus {
  upload_id: string
  name: string
  size: number
  received: number
  status: 'uploading' | 'complete'
  sha256: string | null
}

export interface CompletedUpload {
  file_id: string
  name: string
  size: number
  sha256: string
}

export function createUpload(name: string, size: number): Promise<UploadSession> {
  return postJson<UploadSession>('/api/uploads', { name, size })
}

export function getUpload(uploadId: string): Promise<UploadStatus> {
  return getJson<UploadStatus>(`/api/uploads/${encodeURIComponent(uploadId)}`)
}

export function putChunk(
  uploadId: string,
  offset: number,
  chunk: Blob,
): Promise<{ upload_id: string; received: number; size: number }> {
  return putBlob(`/api/uploads/${encodeURIComponent(uploadId)}?offset=${offset}`, chunk)
}

export function completeUpload(uploadId: string): Promise<CompletedUpload> {
  return postJson<CompletedUpload>(`/api/uploads/${encodeURIComponent(uploadId)}/complete`, {})
}

export function cancelUpload(uploadId: string): Promise<{ deleted: string }> {
  return deleteJson(`/api/uploads/${encodeURIComponent(uploadId)}`)
}

/** 页面卸载时使用：keepalive 让 DELETE 不随文档销毁被中止。 */
export function cancelUploadKeepalive(uploadId: string): void {
  void fetch(`/api/uploads/${encodeURIComponent(uploadId)}`, {
    method: 'DELETE',
    keepalive: true,
  }).catch(() => undefined)
}
