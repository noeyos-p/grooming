'use client'
import { useCallback, useState } from 'react'
import { GenerateResponse } from '@/types'

export function useGenerate() {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<GenerateResponse | null>(null)

  const generate = useCallback(async (imageUrl: string, breedId: string, styleId: string) => {
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const res = await fetch('/api/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_url: imageUrl, breed_id: breedId, style_id: styleId }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error ?? '변환 중 오류가 발생했습니다.')
      setResult(data as GenerateResponse)
    } catch (e) {
      setError(e instanceof Error ? e.message : '알 수 없는 오류가 발생했습니다.')
    } finally {
      setLoading(false)
    }
  }, [])

  return { generate, loading, error, result }
}
