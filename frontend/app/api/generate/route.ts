import { NextRequest, NextResponse } from 'next/server'
import { GenerateRequest } from '@/types'

export async function POST(request: NextRequest) {
  try {
    const body: GenerateRequest = await request.json()
    const apiUrl = process.env.NEXT_PUBLIC_API_URL

    const res = await fetch(`${apiUrl}/api/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })

    if (!res.ok) {
      return NextResponse.json(
        { error: '이미지 변환 중 오류가 발생했습니다. 다시 시도해 주세요.' },
        { status: res.status }
      )
    }

    const data = await res.json()
    return NextResponse.json(data)
  } catch {
    return NextResponse.json(
      { error: '서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.' },
      { status: 503 }
    )
  }
}
