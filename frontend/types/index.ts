export type Style = {
  id: string
  name: string
  thumbnail_url?: string
}

export type Breed = {
  id: string
  name: string
  styles: Style[]
}

export type GenerateRequest = {
  image_url: string
  breed_id: string
  style_id: string
}

export type GenerateResponse = {
  result_url: string
  processing_time: number
}
