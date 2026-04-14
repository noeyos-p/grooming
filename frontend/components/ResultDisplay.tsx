'use client'
import Image from 'next/image'
import { GenerateResponse } from '@/types'

interface ResultDisplayProps {
  result: GenerateResponse
  onRetry: () => void
}

export function ResultDisplay({ result, onRetry }: ResultDisplayProps) {
  const handleDownload = async () => {
    const res = await fetch(result.result_url)
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'grooming-result.jpg'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="flex flex-col items-center gap-6">
      <div className="relative w-full max-w-md aspect-square rounded-2xl overflow-hidden shadow-lg">
        <Image src={result.result_url} alt="미용 스타일 변환 결과" fill className="object-cover" />
      </div>

      {result.processing_time && (
        <p className="text-xs text-neutral-400">처리 시간: {result.processing_time.toFixed(1)}초</p>
      )}

      <div className="flex gap-3 w-full max-w-md">
        <button
          onClick={handleDownload}
          className="flex-1 bg-[#00D3D7] hover:bg-[#00D3D7]/90 text-white font-semibold py-3 rounded-xl transition-colors"
        >
          다운로드
        </button>
        <button
          onClick={onRetry}
          className="flex-1 border-2 border-neutral-200 hover:border-[#00D3D7]/50 text-neutral-600 font-semibold py-3 rounded-xl transition-colors"
        >
          다시 시도
        </button>
      </div>
    </div>
  )
}
