'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { ImageUploader } from '@/components/ImageUploader'
import { BreedSelector } from '@/components/BreedSelector'
import { StyleSelector } from '@/components/StyleSelector'
import { useImageUpload } from '@/hooks/useImageUpload'
import { useBreedStyles } from '@/hooks/useBreedStyles'

export default function HomePage() {
  const router = useRouter()
  const { file, previewUrl, error: uploadError, handleFile, reset } = useImageUpload()
  const { breeds, loading: breedsLoading } = useBreedStyles()
  const [selectedBreedId, setSelectedBreedId] = useState<string | null>(null)
  const [selectedStyleId, setSelectedStyleId] = useState<string | null>(null)

  const selectedBreed = breeds.find((b) => b.id === selectedBreedId)
  const canSubmit = !!file && !!selectedBreedId && !!selectedStyleId

  const handleBreedSelect = (breedId: string) => {
    setSelectedBreedId(breedId)
    setSelectedStyleId(null)
  }

  const handleSubmit = () => {
    if (!file || !selectedBreedId || !selectedStyleId) return
    const reader = new FileReader()
    reader.onload = () => {
      sessionStorage.setItem('uploadedImage', reader.result as string)
      sessionStorage.setItem('breedId', selectedBreedId)
      sessionStorage.setItem('styleId', selectedStyleId)
      router.push('/result')
    }
    reader.readAsDataURL(file)
  }

  return (
    <main className="min-h-screen bg-white">
      {/* 헤더 */}
      <header className="border-b border-neutral-100 px-6 py-4">
        <h1 className="text-xl font-bold text-neutral-900">
          Grooming <span className="text-[#00D3D7]">Style</span>
        </h1>
      </header>

      <div className="max-w-4xl mx-auto px-6 py-8">
        <div className="mb-8 text-center">
          <p className="text-neutral-500 text-sm">강아지 사진을 올리고 원하는 미용 스타일을 선택하세요</p>
        </div>

        <div className="flex flex-col md:flex-row gap-8">
          {/* 좌: 이미지 업로드 */}
          <div className="md:w-64 flex-shrink-0">
            <ImageUploader
              previewUrl={previewUrl}
              error={uploadError}
              onFile={handleFile}
              onReset={reset}
            />
          </div>

          {/* 우: 견종 + 스타일 선택 */}
          <div className="flex-1 flex flex-col gap-6">
            {breedsLoading ? (
              <div className="flex items-center gap-2 text-sm text-neutral-400">
                <div className="w-4 h-4 border-2 border-[#00D3D7]/30 border-t-[#00D3D7] rounded-full animate-spin" />
                견종 목록 로딩 중...
              </div>
            ) : (
              <BreedSelector
                breeds={breeds}
                selectedId={selectedBreedId}
                onSelect={handleBreedSelect}
              />
            )}

            {selectedBreed && (
              <StyleSelector
                styles={selectedBreed.styles}
                selectedId={selectedStyleId}
                onSelect={setSelectedStyleId}
              />
            )}
          </div>
        </div>

        {/* 변환하기 버튼 */}
        <div className="mt-10 flex justify-center">
          <button
            onClick={handleSubmit}
            disabled={!canSubmit}
            className={`px-12 py-4 rounded-2xl font-bold text-base transition-all
              ${canSubmit
                ? 'bg-[#00D3D7] hover:bg-[#00D3D7]/90 text-white shadow-md hover:shadow-lg'
                : 'bg-[#00D3D7]/30 text-white/70 cursor-not-allowed'
              }`}
          >
            변환하기 →
          </button>
        </div>
      </div>
    </main>
  )
}
