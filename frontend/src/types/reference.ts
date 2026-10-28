import type { FileInfo } from './case-info'

export interface ReferenceRow {
  asset: string
  role: string
  kind?: 'video' | 'image'
  note?: string
  number?: number
  file?: FileInfo | null
  url?: string | null
  exists?: boolean
  roleFixed?: boolean
  locked?: boolean
  roleLabel?: string
  noteLabel?: string
}
