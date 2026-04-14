import { NextResponse } from 'next/server'
import { Breed } from '@/types'

const FALLBACK_BREEDS: Breed[] = [
  { id: 'maltese', name: '말티즈', styles: [
    { id: 'teddy_cut', name: '테디베어컷' },
    { id: 'puppy_cut', name: '퍼피컷' },
    { id: 'lion_cut', name: '라이언컷' },
  ]},
  { id: 'poodle', name: '푸들', styles: [
    { id: 'teddy_cut', name: '테디베어컷' },
    { id: 'continental_clip', name: '콘티넨탈클립' },
    { id: 'puppy_cut', name: '퍼피컷' },
  ]},
  { id: 'bichon', name: '비숑', styles: [
    { id: 'round_cut', name: '라운드컷' },
    { id: 'puppy_cut', name: '퍼피컷' },
    { id: 'teddy_cut', name: '테디베어컷' },
  ]},
  { id: 'maltipoo', name: '말티푸', styles: [
    { id: 'teddy_cut', name: '테디베어컷' },
    { id: 'puppy_cut', name: '퍼피컷' },
    { id: 'fluffy_cut', name: '플러피컷' },
  ]},
  { id: 'pomeranian', name: '포메라니안', styles: [
    { id: 'bear_cut', name: '곰돌이컷' },
    { id: 'fox_cut', name: '여우컷' },
    { id: 'round_cut', name: '라운드컷' },
  ]},
  { id: 'yorkshire', name: '요크셔테리어', styles: [
    { id: 'puppy_cut', name: '퍼피컷' },
    { id: 'show_cut', name: '쇼컷' },
    { id: 'teddy_cut', name: '테디베어컷' },
  ]},
  { id: 'shih_tzu', name: '시츄', styles: [
    { id: 'puppy_cut', name: '퍼피컷' },
    { id: 'teddy_cut', name: '테디베어컷' },
    { id: 'lion_cut', name: '라이언컷' },
  ]},
  { id: 'papillon', name: '파피용', styles: [
    { id: 'natural_cut', name: '자연컷' },
    { id: 'puppy_cut', name: '퍼피컷' },
    { id: 'summer_cut', name: '썸머컷' },
  ]},
  { id: 'spitz', name: '스피츠', styles: [
    { id: 'natural_cut', name: '자연컷' },
    { id: 'round_cut', name: '라운드컷' },
    { id: 'bear_cut', name: '곰돌이컷' },
  ]},
  { id: 'mini_bichon', name: '미니비숑', styles: [
    { id: 'round_cut', name: '라운드컷' },
    { id: 'teddy_cut', name: '테디베어컷' },
    { id: 'puppy_cut', name: '퍼피컷' },
  ]},
  { id: 'bedlington', name: '베들링턴', styles: [
    { id: 'traditional_cut', name: '전통컷' },
    { id: 'puppy_cut', name: '퍼피컷' },
    { id: 'lamb_cut', name: '램컷' },
  ]},
]

export async function GET() {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL
  try {
    const res = await fetch(`${apiUrl}/api/breeds`, { next: { revalidate: 3600 } })
    if (!res.ok) throw new Error('Backend unavailable')
    const data = await res.json()
    return NextResponse.json(data)
  } catch {
    return NextResponse.json(FALLBACK_BREEDS)
  }
}
