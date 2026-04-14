'use client'
import { useState, useEffect } from 'react'
import { Breed } from '@/types'

export function useBreedStyles() {
  const [breeds, setBreeds] = useState<Breed[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/breeds')
      .then((res) => {
        if (!res.ok) throw new Error('Failed to fetch breeds')
        return res.json()
      })
      .then((data: Breed[]) => setBreeds(data))
      .catch(() => setError('견종 목록을 불러오지 못했습니다.'))
      .finally(() => setLoading(false))
  }, [])

  return { breeds, loading, error }
}
