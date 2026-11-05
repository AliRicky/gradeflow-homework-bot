-- Base schema for Grade Flow App

CREATE TABLE IF NOT EXISTS public.users (
  id text PRIMARY KEY,
  full_name text,
  role text,
  student_id text,
  section text,
  raw_password text
);

CREATE TABLE IF NOT EXISTS public.assignments (
  id text PRIMARY KEY,
  short_code text,
  title text,
  type text,
  due_date text,
  weight_percent numeric,
  status text,
  total_max_score numeric,
  questions jsonb
);

CREATE TABLE IF NOT EXISTS public.regrades (
  id text PRIMARY KEY,
  student_id text,
  student_name text,
  assignment_id text,
  assignment_title text,
  question_label text,
  current_score numeric,
  max_score numeric,
  reason text,
  status text,
  ta_response text,
  created_at text
);

CREATE TABLE IF NOT EXISTS public.submissions (
  id integer PRIMARY KEY,
  chat_id bigint,
  student_id text,
  hw_num integer,
  message_id bigint,
  file_id text,
  file_name text,
  submitted_at text,
  edit_count integer DEFAULT 0
);

CREATE TABLE IF NOT EXISTS public.user_state (
  chat_id bigint PRIMARY KEY,
  selected_hw integer,
  attempts integer DEFAULT 0,
  is_blacklisted boolean DEFAULT false
);

CREATE TABLE IF NOT EXISTS public.students (
  chat_id bigint PRIMARY KEY,
  student_id text,
  name text,
  registered_at text
);

CREATE TABLE IF NOT EXISTS public.grades (
  student_id text,
  assignment_id text,
  scores jsonb,
  graded_by text,
  graded_at text,
  PRIMARY KEY (student_id, assignment_id)
);
