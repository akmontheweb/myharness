---
applies_to: [spring]
---

## Java — Spring Boot

### When this skill applies
The workspace has a `pom.xml` (Maven) or `build.gradle`/`build.gradle.kts` (Gradle) declaring `spring-boot-starter-*` dependencies, plus an entrypoint class annotated with `@SpringBootApplication`.

### File layout (idiomatic)
```
src/main/java/com/example/app/
  Application.java          # @SpringBootApplication + main(String[])
  controller/               # @RestController classes
  service/                  # @Service business logic
  repository/               # @Repository / Spring Data JPA interfaces
  domain/                   # entity classes (@Entity)
  dto/                      # request/response records
  config/                   # @Configuration beans
src/main/resources/
  application.yml           # or application.properties
  application-dev.yml
  db/migration/             # Flyway: V1__init.sql, V2__add_users.sql
src/test/java/com/example/app/
  ...mirror of main
pom.xml or build.gradle(.kts)
```

### Conventions to follow
- Constructor injection only — `@Autowired` on fields is discouraged. Use `final` fields + a single constructor (Lombok `@RequiredArgsConstructor` or explicit).
- DTOs as Java `record`s (Java 16+) for immutability and pattern-matching ergonomics.
- Repositories extend `JpaRepository<Entity, IdType>` — don't write boilerplate CRUD.
- Configuration goes in `application.yml`, not hardcoded. Profile-specific overrides in `application-{profile}.yml`.
- `@Transactional` at the service layer, not the controller.
- Lombok `@Data` is risky on JPA entities (breaks equality contract). Use `@Getter @Setter @ToString(exclude="lazyField")` or explicit methods.

### Common patches the LLM gets wrong
- Field-injected `@Autowired` instead of constructor injection.
- Returning JPA entities directly from controllers (leaks lazy-load proxies into JSON). Return DTOs/records.
- Skipping `@Transactional` on multi-entity write operations.
- Adding endpoints under the wrong package (Spring Boot's component scan defaults to `Application.java`'s package and below).

### Migrations
- Flyway: place `V<n>__description.sql` files in `src/main/resources/db/migration/`. Spring Boot runs them on startup.
- Liquibase: `db/changelog/db.changelog-master.xml` + per-feature changelogs.
- Build/test command typically: `./mvnw verify` (Maven) or `./gradlew check` (Gradle). Both run unit tests, integration tests, and applicable static analysis (e.g. SpotBugs, Checkstyle).
