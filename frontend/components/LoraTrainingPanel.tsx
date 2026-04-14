'use client'
import { useRef, useState } from 'react'
import { Dialog } from '@base-ui/react/dialog'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export interface LoraTrainingPanelProps {
  breedId: string
  styleId: string
  breedName: string
  styleName: string
  open: boolean
  onClose: () => void
  onTrainingStarted: (trainingId: string) => void
}

const MAX_SIZE_BYTES = 200 * 1024 * 1024

export function LoraTrainingPanel({
  breedId,
  styleId,
  breedName,
  styleName,
  open,
  onClose,
  onTrainingStarted,
}: LoraTrainingPanelProps) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  function validateAndSetFile(file: File) {
    setFileError(null)
    setSubmitError(null)
    if (!file.name.endsWith('.zip')) {
      setFileError('.zip 파일만 업로드할 수 있습니다.')
      setSelectedFile(null)
      return
    }
    if (file.size > MAX_SIZE_BYTES) {
      setFileError('파일 크기는 200MB 이하여야 합니다.')
      setSelectedFile(null)
      return
    }
    setSelectedFile(file)
  }

  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (file) validateAndSetFile(file)
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files?.[0]
    if (file) validateAndSetFile(file)
  }

  function handleDragOver(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setDragOver(true)
  }

  function handleDragLeave() {
    setDragOver(false)
  }

  async function handleSubmit() {
    if (!selectedFile) return
    setUploading(true)
    setSubmitError(null)
    try {
      const formData = new FormData()
      formData.append('breed_id', breedId)
      formData.append('style_id', styleId)
      formData.append('images', selectedFile)

      const res = await fetch('http://localhost:8000/api/admin/train', {
        method: 'POST',
        body: formData,
      })

      if (!res.ok) {
        const text = await res.text()
        throw new Error(text || `서버 오류 (${res.status})`)
      }

      const data = (await res.json()) as { training_id: string; message: string }
      onTrainingStarted(data.training_id)
      onClose()
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : '학습 시작 중 오류가 발생했습니다.')
    } finally {
      setUploading(false)
    }
  }

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) {
      setSelectedFile(null)
      setFileError(null)
      setSubmitError(null)
      setUploading(false)
      onClose()
    }
  }

  function formatFileSize(bytes: number): string {
    if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)}MB`
    return `${(bytes / 1024).toFixed(0)}KB`
  }

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange} modal>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm" />
        <Dialog.Popup className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="w-full max-w-md rounded-2xl bg-white shadow-xl">
            <div className="px-6 pt-6 pb-2">
              <Dialog.Title className="text-lg font-bold text-neutral-900">
                {breedName} x {styleName} 학습
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-sm text-neutral-500">
                학습용 이미지 ZIP 파일을 업로드하세요 (최대 200MB)
              </Dialog.Description>
            </div>

            <div className="px-6 py-4">
              <div
                onClick={() => inputRef.current?.click()}
                onDrop={handleDrop}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                className={cn(
                  'flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-10 transition-colors',
                  dragOver
                    ? 'border-[#00D3D7] bg-[#00D3D7]/10'
                    : 'border-neutral-200 hover:border-[#00D3D7]/50 hover:bg-neutral-50'
                )}
              >
                <input
                  ref={inputRef}
                  type="file"
                  accept=".zip"
                  className="hidden"
                  onChange={handleInputChange}
                />
                {selectedFile ? (
                  <div className="text-center">
                    <p className="text-sm font-medium text-neutral-900 break-all">
                      {selectedFile.name}
                    </p>
                    <p className="mt-1 text-xs text-neutral-400">
                      {formatFileSize(selectedFile.size)}
                    </p>
                    <p className="mt-2 text-xs text-[#00D3D7]">클릭하여 파일 변경</p>
                  </div>
                ) : (
                  <div className="text-center">
                    <p className="text-sm text-neutral-500">
                      클릭하거나 파일을 드래그하여 업로드
                    </p>
                    <p className="mt-1 text-xs text-neutral-400">.zip 파일 · 최대 200MB</p>
                  </div>
                )}
              </div>

              {fileError && (
                <p className="mt-2 text-xs text-red-500">{fileError}</p>
              )}

              {submitError && (
                <div className="mt-3 rounded-lg bg-red-50 px-3 py-2">
                  <p className="text-xs text-red-600">{submitError}</p>
                </div>
              )}
            </div>

            <div className="flex justify-end gap-2 border-t border-neutral-100 px-6 py-4">
              <Dialog.Close
                render={
                  <Button variant="outline" disabled={uploading}>
                    취소
                  </Button>
                }
              />
              <button
                onClick={handleSubmit}
                disabled={!selectedFile || uploading}
                className={cn(
                  'inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium text-white transition-all',
                  !selectedFile || uploading
                    ? 'cursor-not-allowed bg-[#00D3D7]/40'
                    : 'bg-[#00D3D7] hover:bg-[#00D3D7]/90'
                )}
              >
                {uploading && (
                  <span className="size-3.5 rounded-full border-2 border-white/30 border-t-white animate-spin" />
                )}
                {uploading ? '업로드 중...' : '학습 시작'}
              </button>
            </div>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
