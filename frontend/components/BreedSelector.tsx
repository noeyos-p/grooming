'use client'
import { Breed } from '@/types'

interface BreedSelectorProps {
  breeds: Breed[]
  selectedId: string | null
  onSelect: (breedId: string) => void
}

export function BreedSelector({ breeds, selectedId, onSelect }: BreedSelectorProps) {
  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm font-semibold text-neutral-700">견종 선택</p>
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
        {breeds.map((breed) => (
          <button
            key={breed.id}
            onClick={() => onSelect(breed.id)}
            className={`rounded-xl border-2 px-2 py-2 text-xs font-medium transition-all
              ${selectedId === breed.id
                ? 'border-[#00D3D7] bg-[#00D3D7]/10 text-[#00D3D7]'
                : 'border-neutral-200 bg-white text-neutral-600 hover:border-[#00D3D7]/50'
              }`}
          >
            {breed.name}
          </button>
        ))}
      </div>
    </div>
  )
}
