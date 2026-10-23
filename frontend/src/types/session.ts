export interface SessionOption {
  id: number
  name: string
}

export interface EpisodeOption {
  no: number
  id: number
  name: string
  sessions: SessionOption[]
}

export interface ProjectOption {
  id: number
  name: string
  episodes: EpisodeOption[]
}

export interface SessionsResponse {
  fetched_at: string
  projects: ProjectOption[]
  error?: string
}
