'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { LoraTrainingPanel } from '@/components/LoraTrainingPanel'
import { cn } from '@/lib/utils'

// ---------------------------------------------------------------------------
// Static breed + style data
// ---------------------------------------------------------------------------

interface StyleItem {
  id: string
  name: string
}

interface BreedItem {
  id: string
  name: string
  styles: StyleItem[]
}

const BREEDS: BreedItem[] = [
  {
    id: 'maltese',
    name: '말티즈',
    styles: [
      { id: 'teddy_cut', name: '테디베어컷' },
      { id: 'puppy_cut', name: '퍼피컷' },
      { id: 'lion_cut', name: '라이언컷' },
    ],
  },
  {
    id: 'poodle',
    name: '푸들',
    styles: [
      { id: 'teddy_cut', name: '테디베어컷' },
      { id: 'continental_clip', name: '콘티넨탈클립' },
      { id: 'puppy_cut', name: '퍼피컷' },
    ],
  },
  {
    id: 'bichon',
    name: '비숑',
    styles: [
      { id: 'round_cut', name: '라운드컷' },
      { id: 'puppy_cut', name: '퍼피컷' },
      { id: 'teddy_cut', name: '테디베어컷' },
    ],
  },
  {
    id: 'maltipoo',
    name: '말티푸',
    styles: [
      { id: 'teddy_cut', name: '테디베어컷' },
      { id: 'puppy_cut', name: '퍼피컷' },
      { id: 'fluffy_cut', name: '플러피컷' },
    ],
  },
  {
    id: 'pomeranian',
    name: '포메라니안',
    styles: [
      { id: 'bear_cut', name: '곰돌이컷' },
      { id: 'fox_cut', name: '여우컷' },
      { id: 'round_cut', name: '라운드컷' },
    ],
  },
  {
    id: 'yorkshire',
    name: '요크셔테리어',
    styles: [
      { id: 'puppy_cut', name: '퍼피컷' },
      { id: 'show_cut', name: '쇼컷' },
      { id: 'teddy_cut', name: '테디베어컷' },
    ],
  },
  {
    id: 'shih_tzu',
    name: '시츄',
    styles: [
      { id: 'puppy_cut', name: '퍼피컷' },
      { id: 'teddy_cut', name: '테디베어컷' },
      { id: 'lion_cut', name: '라이언컷' },
    ],
  },
  {
    id: 'papillon',
    name: '파피용',
    styles: [
      { id: 'natural_cut', name: '자연컷' },
      { id: 'puppy_cut', name: '퍼피컷' },
      { id: 'summer_cut', name: '썸머컷' },
    ],
  },
  {
    id: 'spitz',
    name: '스피츠',
    styles: [
      { id: 'natural_cut', name: '자연컷' },
      { id: 'round_cut', name: '라운드컷' },
      { id: 'bear_cut', name: '곰돌이컷' },
    ],
  },
  {
    id: 'mini_bichon',
    name: '미니비숑',
    styles: [
      { id: 'round_cut', name: '라운드컷' },
      { id: 'teddy_cut', name: '테디베어컷' },
      { id: 'puppy_cut', name: '퍼피컷' },
    ],
  },
  {
    id: 'bedlington',
    name: '베들링턴',
    styles: [
      { id: 'traditional_cut', name: '전통컷' },
      { id: 'puppy_cut', name: '퍼피컷' },
      { id: 'lamb_cut', name: '램컷' },
    ],
  },
]

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface LoraEntry {
  status: string
  version: string | null
  trigger_word: string
  trained_at: string | null
  replicate_model: string
  training_id: string | null
}

type LoraRegistry = Record<string, LoraEntry>

interface SelectedCard {
  breedId: string
  styleId: string
  breedName: string
  styleName: string
}

type CardStatus = 'none' | 'training' | 'ready' | 'failed'

function getCardStatus(registry: LoraRegistry, breedId: string, styleId: string): CardStatus {
  const key = `${breedId}_${styleId}`
  const entry = registry[key]
  if (!entry) return 'none'
  if (entry.status === 'training') return 'training'
  if (entry.status === 'ready') return 'ready'
  if (entry.status === 'failed') return 'failed'
  return 'none'
}

function getTrainingId(registry: LoraRegistry, breedId: string, styleId: string): string | null {
  return registry[`${breedId}_${styleId}`]?.training_id ?? null
}

// ---------------------------------------------------------------------------
// StatusBadge
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: CardStatus }) {
  if (status === 'none') {
    return (
      <Badge variant="secondary" className="text-xs">
        미학습
      </Badge>
    )
  }
  if (status === 'training') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-[#00D3D7]/20 px-2 py-0.5 text-xs font-medium text-[#00D3D7] animate-pulse">
        학습중
      </span>
    )
  }
  if (status === 'ready') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-neutral-100 px-2 py-0.5 text-xs font-medium text-neutral-700">
        완료
      </span>
    )
  }
  return (
    <Badge variant="destructive" className="text-xs">
      실패
    </Badge>
  )
}

// ---------------------------------------------------------------------------
// Admin page
// ---------------------------------------------------------------------------

const POLL_INTERVAL_MS = 30_000

export default function AdminPage() {
  const [registry, setRegistry] = useState<LoraRegistry>({})
  const [loading, setLoading] = useState(true)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [selectedCard, setSelectedCard] = useState<SelectedCard | null>(null)
  const pollingTimers = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map())

  const fetchRegistry = useCallback(async () => {
    try {
      setFetchError(null)
      const res = await fetch('http://localhost:8000/api/admin/lora')
      if (!res.ok) throw new Error(`서버 오류 (${res.status})`)
      const data = (await res.json()) as LoraRegistry
      setRegistry(data)
    } catch (err) {
      setFetchError(err instanceof Error ? err.message : '레지스트리를 불러오지 못했습니다.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchRegistry()
  }, [fetchRegistry])

  // Clean up all polling timers on unmount
  useEffect(() => {
    const timers = pollingTimers.current
    return () => {
      timers.forEach((timer) => clearInterval(timer))
    }
  }, [])

  function startPolling(trainingId: string, breedId: string, styleId: string) {
    const key = `${breedId}_${styleId}`
    if (pollingTimers.current.has(key)) return

    const timer = setInterval(async () => {
      try {
        const res = await fetch(
          `http://localhost:8000/api/admin/train/${trainingId}/status`
        )
        if (!res.ok) return
        const data = (await res.json()) as { status: string; version: string | null; logs: string | null }

        if (data.status === 'succeeded' || data.status === 'failed') {
          clearInterval(timer)
          pollingTimers.current.delete(key)
          await fetchRegistry()
        }
      } catch {
        // silently ignore polling errors — retry on next tick
      }
    }, POLL_INTERVAL_MS)

    pollingTimers.current.set(key, timer)
  }

  function handleTrainingStarted(trainingId: string, breedId: string, styleId: string) {
    // Optimistically mark the card as training in the registry
    const key = `${breedId}_${styleId}`
    setRegistry((prev) => {
      const existing = prev[key]
      const updated: LoraEntry = {
        status: 'training',
        version: existing?.version ?? null,
        trigger_word: existing?.trigger_word ?? '',
        trained_at: existing?.trained_at ?? null,
        replicate_model: existing?.replicate_model ?? '',
        training_id: trainingId,
      }
      return { ...prev, [key]: updated }
    })
    startPolling(trainingId, breedId, styleId)
  }

  function handleRefresh() {
    setLoading(true)
    fetchRegistry()
  }

  return (
    <main className="min-h-screen bg-white">
      <header className="border-b border-neutral-100 px-6 py-4">
        <h1 className="text-xl font-bold text-neutral-900">
          Grooming <span className="text-[#00D3D7]">Style</span>
        </h1>
      </header>

      <div className="max-w-6xl mx-auto px-6 py-8">
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-2xl font-bold text-neutral-900">LoRA 학습 관리</h2>
          <Button
            variant="outline"
            size="sm"
            onClick={handleRefresh}
            disabled={loading}
            className={cn(loading && 'opacity-60 pointer-events-none')}
          >
            {loading ? (
              <span className="flex items-center gap-1.5">
                <span className="size-3.5 rounded-full border-2 border-neutral-300 border-t-neutral-600 animate-spin" />
                로딩 중...
              </span>
            ) : (
              '전체 새로고침'
            )}
          </Button>
        </div>

        {fetchError && (
          <div className="mb-6 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-600">
            {fetchError}
          </div>
        )}

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {BREEDS.flatMap((breed) =>
            breed.styles.map((style) => {
              const status = getCardStatus(registry, breed.id, style.id)
              const trainingId = getTrainingId(registry, breed.id, style.id)
              const isDisabled = status === 'training' || status === 'ready'

              return (
                <Card key={`${breed.id}_${style.id}`} size="sm">
                  <CardHeader className="border-b">
                    <div className="flex items-start justify-between gap-2">
                      <CardTitle className="leading-tight">
                        {breed.name}
                        <span className="block text-xs font-normal text-neutral-400 mt-0.5">
                          {style.name}
                        </span>
                      </CardTitle>
                      <StatusBadge status={status} />
                    </div>
                  </CardHeader>

                  {status === 'training' && trainingId && (
                    <CardContent>
                      <p className="text-xs text-neutral-400 break-all">
                        ID: {trainingId}
                      </p>
                    </CardContent>
                  )}

                  <CardFooter className="justify-end">
                    <button
                      disabled={isDisabled}
                      onClick={() =>
                        setSelectedCard({
                          breedId: breed.id,
                          styleId: style.id,
                          breedName: breed.name,
                          styleName: style.name,
                        })
                      }
                      className={cn(
                        'rounded-lg px-3 py-1.5 text-xs font-medium transition-all',
                        isDisabled
                          ? 'cursor-not-allowed bg-neutral-100 text-neutral-300'
                          : 'bg-[#00D3D7] text-white hover:bg-[#00D3D7]/90'
                      )}
                    >
                      학습 시작
                    </button>
                  </CardFooter>
                </Card>
              )
            })
          )}
        </div>
      </div>

      {selectedCard && (
        <LoraTrainingPanel
          breedId={selectedCard.breedId}
          styleId={selectedCard.styleId}
          breedName={selectedCard.breedName}
          styleName={selectedCard.styleName}
          open={true}
          onClose={() => setSelectedCard(null)}
          onTrainingStarted={(trainingId) => {
            handleTrainingStarted(trainingId, selectedCard.breedId, selectedCard.styleId)
            setSelectedCard(null)
          }}
        />
      )}
    </main>
  )
}
