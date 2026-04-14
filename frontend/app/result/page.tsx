'use client'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ResultDisplay } from '@/components/ResultDisplay'
import { useGenerate } from '@/hooks/useGenerate'

export default function ResultPage() {
  const router = useRouter()
  const { generate, loading, error, result } = useGenerate()
  const [started, setStarted] = useState(false)

  useEffect(() => {
    if (started) return
    setStarted(true)

    const imageData = sessionStorage.getItem('uploadedImage')
    const breedId = sessionStorage.getItem('breedId')
    const styleId = sessionStorage.getItem('styleId')

    if (!imageData || !breedId || !styleId) {
      router.replace('/')
      return
    }

    // TODO: 실제 서비스에서는 Cloudinary에 먼저 업로드 후 URL 전달
    // 현재는 base64 dataURL을 image_url로 전달
    generate(imageData, breedId, styleId)
  }, [started, generate, router])

  const handleRetry = () => {
    router.push('/')
  }

  return (
    <main className="min-h-screen bg-white">
      <header className="border-b border-neutral-100 px-6 py-4">
        <h1 className="text-xl font-bold text-neutral-900">
          Grooming <span className="text-[#00D3D7]">Style</span>
        </h1>
      </header>

      <div className="max-w-2xl mx-auto px-6 py-12">
        {loading && (
          <div className="flex flex-col items-center gap-6">
            <div className="w-16 h-16 border-4 border-[#00D3D7]/20 border-t-[#00D3D7] rounded-full animate-spin" />
            <div className="text-center">
              <p className="text-neutral-700 font-medium">미용 스타일을 변환하는 중...</p>
              <p className="text-neutral-400 text-sm mt-1">15~30초 정도 소요될 수 있습니다</p>
            </div>
          </div>
        )}

        {error && !loading && (
          <div className="flex flex-col items-center gap-6">
            <div className="w-16 h-16 rounded-full bg-red-50 flex items-center justify-center">
              <svg width="32" height="32" fill="none" stroke="#ef4444" strokeWidth="2" viewBox="0 0 24 24">
                <circle cx="12" cy="12" r="10"/>
                <line x1="12" y1="8" x2="12" y2="12"/>
                <line x1="12" y1="16" x2="12.01" y2="16"/>
              </svg>
            </div>
            <div className="text-center">
              <p className="text-neutral-700 font-medium">{error}</p>
            </div>
            <button
              onClick={handleRetry}
              className="px-8 py-3 rounded-xl border-2 border-neutral-200 hover:border-[#00D3D7]/50 text-neutral-600 font-semibold transition-colors"
            >
              다시 시도
            </button>
          </div>
        )}

        {result && !loading && (
          <ResultDisplay result={result} onRetry={handleRetry} />
        )}
      </div>
    </main>
  )
}
