export type Locale = 'fr' | 'en';
export interface Attribution {
  author: string;
  credit: string;
  source: string;
  license: string;
  license_url: string;
  changes: string;
}
export interface Illustration {
  url: string;
  large_url?: string;
  width: number;
  height: number;
  fallback: boolean;
  caption: string;
  attribution: Attribution | null;
}
export interface Category {
  id: string;
  name: string;
  icon: string;
  active: boolean;
}
export interface Site {
  name: string;
  categories: Category[];
  streams: { primary: string; android: string };
}
export interface Article {
  id: string;
  hash: string;
  title: string;
  original_title: string;
  summary: string;
  link: string | null;
  source: string;
  category: string;
  published_at: string | null;
  published_raw: string;
  observed_at: string | null;
  observed_raw: string;
  image: Illustration;
}
export interface Coverage {
  id: string;
  title: string;
  source: string;
  link: string | null;
  published_at: string | null;
  published_raw: string;
}
export interface EditorialEvent {
  event_id: string;
  title: string;
  category: string;
  secondary_categories: string[];
  article_count: number;
  domain_count: number;
  sources: string[];
  articles: Coverage[];
  last_updated_at: string | null;
  independent_confirmation: false;
  image: Illustration;
}
export interface PageResult {
  total: number;
  page: number;
  per_page: number;
  observed_at: string;
}
export interface Articles extends PageResult {
  articles: Article[];
  date: string;
}
export interface Events extends PageResult {
  events: EditorialEvent[];
  index_available: boolean;
}
export interface ArchiveDay {
  date: string;
  articles: boolean;
  report: boolean;
  editions: string[];
}
export interface Archives extends PageResult {
  days: ArchiveDay[];
}
export interface RichNode {
  text?: string;
  tag?: string;
  children?: RichNode[];
  href?: string | null;
}
export interface Document {
  title: string;
  date: string;
  kind: string;
  nodes: RichNode[];
}
export interface Resource<T> {
  data: T | null;
  state: 'available' | 'empty' | 'stale' | 'unavailable';
  fetchedAt: string;
}
