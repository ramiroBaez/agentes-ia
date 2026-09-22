-- Seed data for the Project 7 demo database.
-- Runs automatically the first time the Docker volume is created.
--
-- Tables are PascalCase on purpose: this mirrors the Prisma-style schemas the
-- agent sees in real projects and teaches a nice lesson — in Postgres, quoted
-- PascalCase identifiers ("Estudiante") are case-sensitive, while unquoted
-- ones are folded to lowercase ("estudiante"). The agent prompt reinforces this.

CREATE TABLE "Estudiante" (
    "id"         SERIAL PRIMARY KEY,
    "apellido"   VARCHAR(60)  NOT NULL,
    "nombre"     VARCHAR(60)  NOT NULL,
    "legajo"     VARCHAR(12)  NOT NULL UNIQUE,
    "anio"       INTEGER      NOT NULL
);

CREATE TABLE "Curso" (
    "id"      SERIAL PRIMARY KEY,
    "nombre"  VARCHAR(60) NOT NULL,
    "nivel"   VARCHAR(20) NOT NULL
);

CREATE TABLE "Materia" (
    "id"        SERIAL PRIMARY KEY,
    "nombre"    VARCHAR(80) NOT NULL,
    "cursoId"   INTEGER     NOT NULL REFERENCES "Curso"("id")
);

-- Demo data: a tiny academic system (students, courses, subjects).
INSERT INTO "Estudiante" ("apellido", "nombre", "legajo", "anio") VALUES
    ('Gomez',   'Ana',      'UTN-1001', 1),
    ('Perez',   'Lucas',    'UTN-1002', 1),
    ('Ramirez', 'Sofia',    'UTN-1003', 2),
    ('Olivera', 'Mateo',    'UTN-1004', 2),
    ('Fernandez','Lucia',   'UTN-1005', 3),
    ('Rios',    'Tomas',    'UTN-1006', 3);

INSERT INTO "Curso" ("nombre", "nivel") VALUES
    ('Ingenieria en Sistemas', '1er anio'),
    ('Ingenieria en Sistemas', '2do anio'),
    ('Ingenieria en Sistemas', '3er anio');

INSERT INTO "Materia" ("nombre", "cursoId") VALUES
    ('Algoritmos y Estructuras de Datos', 1),
    ('Matematica para Ingenieria',         1),
    ('Base de Datos',                       2),
    ('Ingenieria de Software',              3);