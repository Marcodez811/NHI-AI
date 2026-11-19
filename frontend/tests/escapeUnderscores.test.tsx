import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MessageResponse } from "@/components/ai-elements/message";
import { escapeUnderscores } from "@/lib/escapeUnderscores";

const NAME = "(醫管組)1150712_台灣醫事法律學會-AI智慧健保治理-談參資料(草案)_1150623奉核.docx";

describe("escapeUnderscores", () => {
  it("escapes underscores outside code only", () => {
    expect(escapeUnderscores("a_b `c_d` e_f")).toBe("a\\_b `c_d` e\\_f");
    expect(escapeUnderscores("x\n```\na_b\n```\ny_z")).toBe("x\n```\na_b\n```\ny\\_z");
    expect(escapeUnderscores("```\na_b")).toBe("```\na_b");
    expect(escapeUnderscores("a `b_c")).toBe("a `b\\_c");
  });
});

describe("MessageResponse", () => {
  it("renders file names with underscores without emphasis, keeps bold and code", () => {
    const { container } = render(
      <MessageResponse>{`請見【${NAME}】，**重點** 與 \`x_y_z\``}</MessageResponse>
    );
    expect(container.querySelector("em, [data-streamdown=\"em\"]")).toBeNull();
    expect(container.textContent).toContain(NAME);
    expect(container.querySelector("strong, [data-streamdown=\"strong\"]")?.textContent).toBe("重點");
    expect(container.textContent).toContain("x_y_z");
  });
});
