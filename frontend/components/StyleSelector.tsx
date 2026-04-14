'use client'
import { Style } from '@/types'

interface StyleSelectorProps {
  styles: Style[]
  selectedId: string | null
  onSelect: (styleId: string) => void
}

export function StyleSelector({ styles, selectedId, onSelect }: StyleSelectorProps) {
  if (styles.length === 0) return null

  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm font-semibold text-neutral-700">스타일 선택</p>
      <div className="grid grid-cols-3 gap-3">
        {styles.map((style) => (
          <button
            key={style.id}
            onClick={() => onSelect(style.id)}
            className={`rounded-xl border-2 aspect-square flex items-center justify-center text-xs font-medium transition-all p-2
              ${selectedId === style.id
                ? 'border-[#00D3D7] bg-[#00D3D7]/10 text-[#00D3D7]'
                : 'border-neutral-200 bg-white text-neutral-600 hover:border-[#00D3D7]/50'
              }`}
          >
            {style.name}
          </button>
        ))}
      </div>
    </div>
  )
}
