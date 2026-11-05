-- Supabase seed data for local development testing
-- This file is executed automatically when running `supabase start` or `supabase db reset`

-- Note: In Supabase, inserting directly to auth.users bypasses some of the internal triggers
-- that create auth.identities, but it is sufficient for local development authentication.

INSERT INTO auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at, 
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at
)
VALUES
(
  '00000000-0000-0000-0000-000000000000', 
  gen_random_uuid(), 
  'authenticated', 
  'authenticated', 
  'admin@gradeflow.local', 
  crypt('AdminPassword123!', gen_salt('bf')), 
  NOW(), 
  '{"provider":"email","providers":["email"]}', 
  '{"role": "admin", "full_name": "Course Administrator", "student_id": "Head TA"}', 
  NOW(), 
  NOW()
),
(
  '00000000-0000-0000-0000-000000000000', 
  gen_random_uuid(), 
  'authenticated', 
  'authenticated', 
  'student1@gradeflow.local', 
  crypt('password123', gen_salt('bf')), 
  NOW(), 
  '{"provider":"email","providers":["email"]}', 
  '{"role": "student", "full_name": "Test Student 1", "student_id": "4042019001"}', 
  NOW(), 
  NOW()
),
(
  '00000000-0000-0000-0000-000000000000', 
  gen_random_uuid(), 
  'authenticated', 
  'authenticated', 
  'student2@gradeflow.local', 
  crypt('password123', gen_salt('bf')), 
  NOW(), 
  '{"provider":"email","providers":["email"]}', 
  '{"role": "student", "full_name": "Test Student 2", "student_id": "4042019002"}', 
  NOW(), 
  NOW()
),
(
  '00000000-0000-0000-0000-000000000000', 
  gen_random_uuid(), 
  'authenticated', 
  'authenticated', 
  'student3@gradeflow.local', 
  crypt('password123', gen_salt('bf')), 
  NOW(), 
  '{"provider":"email","providers":["email"]}', 
  '{"role": "student", "full_name": "Test Student 3", "student_id": "4042019003"}', 
  NOW(), 
  NOW()
);
