'use client'
import { useState, useCallback } from 'react'

const MAX_SIZE_MB = 5
const ALLOWED_TYPES = ['image/jpeg', 'image/png']

export function useImageUpload() {
  const [file, setFile] = useState<File | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const validate = useCallback((f: File): string | null => {
    if (!ALLOWED_TYPES.includes(f.type)) return 'JPG 또는 PNG 파일만 업로드할 수 있습니다.'
    if (f.size > MAX_SIZE_MB * 1024 * 1024) return `파일 크기는 ${MAX_SIZE_MB}MB 이하여야 합니다.`
    return null
  }, [])

  const handleFile = useCallback((f: File) => {
    const validationError = validate(f)
    if (validationError) {
      setError(validationError)
      return
    }
    setError(null)
    setFile(f)
    const url = URL.createObjectURL(f)
    setPreviewUrl(url)
  }, [validate])

  const reset = useCallback(() => {
    if (previewUrl) URL.revokeObjectURL(previewUrl)
    setFile(null)
    setPreviewUrl(null)
    setError(null)
  }, [previewUrl])

  return { file, previewUrl, error, handleFile, reset }
}
