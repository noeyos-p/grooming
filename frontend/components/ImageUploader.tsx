'use client'
import { useCallback, useRef } from 'react'
import Image from 'next/image'

interface ImageUploaderProps {
  previewUrl: string | null
  error: string | null
  onFile: (file: File) => void
  onReset: () => void
}

export function ImageUploader({ previewUrl, error, onFile, onReset }: ImageUploaderProps) {
  const inputRef = useRef<HTMLInputElement>(null)

  const handleDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    const f = e.dataTransfer.files[0]
    if (f) onFile(f)
  }, [onFile])

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) onFile(f)
  }

  return (
    <div className="flex flex-col gap-2">
      <div
        onDrop={handleDrop}
        onDragOver={(e) => e.preventDefault()}
        onClick={() => !previewUrl && inputRef.current?.click()}
        className={`relative w-full aspect-square max-w-sm rounded-2xl border-2 border-dashed transition-colors overflow-hidden
          ${previewUrl ? 'border-[#00D3D7] cursor-default' : 'border-neutral-300 hover:border-[#00D3D7] cursor-pointer bg-neutral-50'}
        `}
      >
        {previewUrl ? (
          <Image src={previewUrl} alt="업로드된 강아지 사진" fill className="object-cover" />
        ) : (
          <div className="flex flex-col items-center justify-center h-full gap-3 p-6 text-center">
            <div className="w-12 h-12 rounded-full bg-[#00D3D7]/10 flex items-center justify-center">
              <svg width="24" height="24" fill="none" stroke="#00D3D7" strokeWidth="2" viewBox="0 0 24 24">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="17 8 12 3 7 8"/>
                <line x1="12" y1="3" x2="12" y2="15"/>
              </svg>
            </div>
            <div>
              <p className="text-sm font-medium text-neutral-700">사진을 드래그하거나 클릭하여 업로드</p>
              <p className="text-xs text-neutral-400 mt-1">JPG, PNG · 최대 10MB</p>
            </div>
          </div>
        )}
      </div>

      {previewUrl && (
        <button
          onClick={onReset}
          className="text-xs text-neutral-400 hover:text-neutral-600 underline self-start"
        >
          다른 사진 선택
        </button>
      )}

      {error && <p className="text-xs text-red-500">{error}</p>}

      <input ref={inputRef} type="file" accept="image/jpeg,image/png" className="hidden" onChange={handleChange} />
    </div>
  )
}
